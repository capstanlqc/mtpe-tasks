#!/usr/bin/env python

import re
import os
import sys
import glob
from tmx2dataframe import tmx2dataframe
import pandas as pd
from rich import print
import nltk
from string import punctuation
import json
from lxml import etree
import Levenshtein
import seaborn as sns
import matplotlib.pyplot as plt
from scipy.stats import pearsonr, spearmanr

# resources 

nltk.download("punkt")
punctuations = list(punctuation)

# constants

omtprj_dpath_parent = sys.argv[2]
omtprj_dname_template = sys.argv[4]
config_fpath = os.path.join(omtprj_dpath_parent, "config.json")
omtprj_dpaths = glob.glob(
    f"{omtprj_dpath_parent}/{omtprj_dname_template}", recursive=True
)

# functions

def extract_date(path):
    match = re.search(r"(\d{8})", path)
    if match:
        return match.group(1)
    return None


def get_target_lang(omtprj_dpath):
    # todo: put this function in a common module
    omtprj_fpath = os.path.join(omtprj_dpath, "omegat.project")

    # load the XML file
    tree = etree.parse(omtprj_fpath)
    root = tree.getroot()

    # use XPath to find the 'source_lang' element
    target_lang = root.xpath("//target_lang/text()")[0]
    return target_lang


def get_total_wc(segments):
    return sum(
        len(
            [
                word
                for word in nltk.word_tokenize(segm.lower())
                if word not in punctuations
            ]
        )
        for segm in segments
    )


def split_note_column(df):
    # Use regex to split the 'note' column into 'score' and 'category'
    df[['score', 'category']] = df['note'].str.extract(r'^(\d+\.\d+):\s*((?:LOW|HIGH) CONFIDENCE$)')
    
    # Convert the 'score' column to float
    df['score'] = df['score'].astype(float)
    
    return df.drop(columns=['note'])



def add_postedited_version(mt_tmx_fpath, pe_tmx_fpath):
    _, mt_df = tmx2dataframe.read(mt_tmx_fpath)
    mt_df = mt_df.rename(columns={'target_sentence': 'mt_version'})
    _, pe_df = tmx2dataframe.read(pe_tmx_fpath)
    pe_df = pe_df.rename(columns={'target_sentence': 'pe_version'})   
    return pd.merge(mt_df, pe_df[['source_sentence', 'pe_version']], on='source_sentence')


def add_edit_distance(df):
    # compare mt_version and pe_version and add new edit_distance column
    # df["edit_distance"] = Levenshtein.ratio(df["mt_version"], df["pe_version"])
    df['edit_distance'] = df.apply(lambda row: Levenshtein.distance(row["mt_version"], row["pe_version"]), axis=1)
    df['similarity_ratio'] = df.apply(lambda row: Levenshtein.ratio(row["mt_version"], row["pe_version"]), axis=1)
    return split_note_column(df)


def add_threshold(df, threshold):
    df['thrshld_deviation'] = df.apply(lambda row: row["score"] - threshold, axis=1)
    return df


def get_config(config_fpath):
    with open(config_fpath, "r") as file:
        return json.load(file)


def draw_plots(data, target_lang):
    # Scatter plot of similarity_ratio vs. score
    plt.figure(figsize=(8, 6))
    sns.scatterplot(data=data, x='similarity_ratio', y='thrshld_deviation', hue='category', palette='Set2')
    plt.title(f"Edit effort vs. Distance from threshold in {target_lang}")
    plt.xlabel("Similarity ratio")
    plt.ylabel("Distance from threshold in QE score")
    plt.grid(True)
    # plt.show()
    plt.savefig(f"analysis/edit_vs_thrshld_dev_{target_lang}.png", dpi=300, bbox_inches="tight")


def save_averages_as_excel(averages):
    print("\n---------------------")
    print("Averages:")
    for x in averages:
        for k,v in x.items():
            print(f"{k}: {v}")

    # convert the list of dictionaries into a DataFrame
    df_list = []
    for item in averages:
        for lang, values in item.items():
            values['language'] = lang  # add the language code as a column
            df_list.append(values)

    df = pd.DataFrame(df_list)
    df = df[['language', 'HIGH CONFIDENCE', 'LOW CONFIDENCE']]
    df.to_excel("analysis/average_conf_scores.xlsx", index=False)
    print("Means saved to 'analysis/average_conf_scores.xlsx'")


def save_data_as_excel(multilingual_data):

    output_file = 'analysis/data.xlsx'
    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        for lang, data in multilingual_data.items():
            data.to_excel(writer, sheet_name=lang, index=False)

    print(f"Data file saved as '{output_file}'")



# action starts

if __name__ != "__main__":
    sys.exit()


data_all_lang = {}
averages = []
config = get_config(config_fpath)


for omtprj_dpath in omtprj_dpaths:

    wc = {}
    print("==============================")
    print(os.path.basename(omtprj_dpath))

    # get translation units with qs score and category from tm/auto/mt/

    # for each MT'ed source text, get PE'ed translation from project-save

    mt_tmx_folder = config["mt_tmx_folder"]
    mt_dpath = os.path.join(omtprj_dpath, mt_tmx_folder)
    mt_tmx_fpaths = glob.glob(f"{mt_dpath}/**.tmx", recursive=True)

    if len(mt_tmx_fpaths) == 0:
        print(f"No MT memory found in {os.path.basename(omtprj_dpath)}, unable to draw initial MT used!")
        continue

    # get the latest
    mt_tmx_fpath = max(mt_tmx_fpaths, key=lambda x: extract_date(x))
    mt_tmx_fname = os.path.basename(mt_tmx_fpath)
    print(f"Initial MT extracted from '{mt_tmx_fname}'")

    wc["tmx_file"] = mt_tmx_fname
    wc["engine"] = mt_tmx_fname.split("_")[0]
    wc["lang"] = mt_tmx_fname.split("_")[1]
    target_lang = get_target_lang(omtprj_dpath)
    assert wc["lang"] == target_lang

    metadata, df = tmx2dataframe.read(mt_tmx_fpath)

    pe_tmx_fpath = os.path.join(omtprj_dpath, "omegat", "project_save.tmx")
    mtpe_df = add_postedited_version(mt_tmx_fpath, pe_tmx_fpath)
    
    threshold = config["quality_estimation"]["thresholds"][target_lang]
    print(f"The threadshold for {target_lang} was {threshold}")

    ed_mtpe_df = add_edit_distance(mtpe_df)
    ed_mtpe_df = add_threshold(ed_mtpe_df, threshold)
    # print(ed_mtpe_df.columns)
    
    data = ed_mtpe_df
    data_all_lang[target_lang] = data

    # draw scatter plots
    draw_plots(data, target_lang)
    # calculate correlation coefficients
    pearson_corr, pearson_pval = pearsonr(data['similarity_ratio'], data['thrshld_deviation'])
    spearman_corr, spearman_pval = spearmanr(data['similarity_ratio'], data['thrshld_deviation'])
    print(f" {pearson_corr=}\n {pearson_pval=}\n {spearman_corr=}\n {spearman_pval=}")

    # averages
    average_similarity_ratio = data.groupby('category')['similarity_ratio'].mean()
    result = {target_lang: average_similarity_ratio.to_dict()}
    averages.append({target_lang: average_similarity_ratio.to_dict()})


save_averages_as_excel(averages)
save_data_as_excel(data_all_lang)
