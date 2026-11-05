
import re
import glob
import os
import pandas as pd
from oceanval.parsers import Validator
definitions = Validator()
tidy_info = {}


# Celsius as files and datasets write it: degC, deg_C, degrees_C, Degree Celsius,
# DEGREES-CELSIUS, "° C", Celsius, centigrade. Case is ignored. A bare "C" is not
# one (it may be carbon), and the letter after the C must not go on, so
# degrees_north and degCm2 are left alone.
CELSIUS = re.compile(
    r"(?<![A-Za-z])(?:"
    r"deg(?:rees?)?[\s_.\-]*(?:celsius|centigrade|c)"
    r"|[°º][\s_.\-]*c"
    r"|celsius|centigrade"
    r")(?![A-Za-z])",
    re.IGNORECASE,
)
# the same, only between brackets, for prose: "(deg_C)", "[degrees C]"
CELSIUS_IN_BRACKETS = re.compile(
    r"([(\[])\s*" + CELSIUS.pattern + r"\s*([)\]])", re.IGNORECASE
)


def celsius(x):
    """x with every way of writing degrees Celsius made °C."""
    return CELSIUS.sub("°C", x)


def fix_unit(x):
    x = x.replace("/m^3", "m<sup>-3</sup>") 
    x = x.replace("/m**3", "m<sup>-3</sup>") 
    x = x.replace("/m3", "m<sup>-3</sup>")
    x = x.replace("m-3", "m<sup>-3</sup>")
    x = x.replace("m-1", "m<sup>-1</sup>")
    x = x.replace("m-2", "m<sup>-2</sup>")
    x = x.replace("/m^2", "m<sup>-2</sup>")
    x = x.replace("/m2", "m<sup>-2</sup>")
    x = x.replace("m2", "m<sup>2</sup>")
    x = x.replace("m3", "m<sup>3</sup>")
    #O_2
    x = x.replace("O_2", "O<sub>2</sub>")
    # fix CO2
    x = x.replace("CO2", "CO<sub>2</sub>")
    # fix /yr
    x = x.replace("/yr", "year<sup>-1</sup>")
    # degC, degrees_C, deg_C, degree_Celsius...
    x = celsius(x)


    return x



def df_display(df):
    # only 2 decimal places
    for col in df.columns:
        if "Number" in col:
            try:
                df[col] = df[col].astype(int)
                # put in commas
                df[col] = df[col].apply(lambda x: "{:,}".format(x))
            except:
                pass
    # if rmsd is a column title change to RMSD
    if "rmsd" in df.columns:
        df = df.rename(columns={"rmsd": "RMSD"})
    # round to 2 decimal places
    df = df.round(2)


    # coerce numeric columns to str
    # if number is in the column title, make sure the variable is int

    df = df.map(lambda x: f"{x:.2f}" if isinstance(x, float) else x)
    # capitalize unit column name, if it exists
    if "unit" in df.columns:
        df = df.rename(columns={"unit": "Unit"})
    # capitalize variable column name, if it exists
    if "variable" in df.columns:
        df = df.rename(columns={"variable": "Variable"})
        # fix variable names
        # capitalize variable
        df["Variable"] = df["Variable"].str.capitalize()
        # ensure "Poc " is "POC "
        # ensure "Doc" is "DOC"
    if "Variable" in df.columns:
        df["Variable"] = df["Variable"].str.replace("pCO2", "pCO<sub>2</sub>")
        df["Variable"] = df["Variable"].str.replace("CO2", "CO<sub>2</sub>")
    # if R2 is in the column name, make sure the 2 is superscript
    if "R2" in df.columns:
        df = df.rename(columns={"R2": "R²"})
    # convert nan to N/A
    df = df.replace("nan", "N/A")
    if "Region" in df.columns:
        if "Full Domain" in df["Region"].values:
            # ensure the Full Domain region is the first row
            # get the index of the row
            i_domain = df[df["Region"] == "Full Domain"].index[0]
            df1 = df.iloc[i_domain:i_domain+1]
            df2 = df.drop(i_domain)
            df = pd.concat([df1, df2])
            df = df.reset_index(drop = True)

    if len(df.columns) > 7:
        # remove concentration from variable
        df = df.assign(Variable = df["Variable"].str.replace(" concentration", ""))

    if "Unit" in df.columns:
        #format this appropriately. Markdown, superscripts etc.
        df["Unit"] = df["Unit"].apply(fix_unit)

    # if "Correlation" in df.columns:
        # change this to r, italicized
    df = df.rename(columns={"Correlation": "<em>r</em>"})
    df = df.rename(columns={"Correlation coefficient": "<em>r</em>"})
    return df.style.hide(axis="index")



from IPython.display import Markdown as md_markdown

def md_basic(x):
    x = x.replace(" 5 m ", " 5m ")
    x = x.replace(" 5 m.", " 5m.")

    x = x.replace("  ", " ")
    # use regex to ensure any numbers have commas
    if "**Figure" in x:
        # ensure the sentence ends with .
        if x[-1] != ".":
            x = x + "."
    if "**Table" in x:
        # ensure the sentence ends with .
        if x[-1] != ".":
            x = x + "."

    x = x.replace(" .", ".")
    x = x.replace(" ,", ",")
    x = x.replace(" :", ":")
    x = x.replace(" ;", ";")
    x = x.replace(" %", "%")

    # ensure there are spaces after commas, using regex
    x = re.sub(r",(\w)", r", \1", x)
    x = x.replace("CO2", "CO<sub>2</sub>")

    return md_markdown(x)

def md(x, number = False):
    x = CELSIUS_IN_BRACKETS.sub(r"\1°C\2", x)

    #model - observation
    x = x.replace("model - observation", "model-observation")
    #98th, handle this kind of thing appropriately with superscripts
    x = x.replace("98th", "98<sup>th</sup>")
    #2nd
    x = x.replace("2nd", "2<sup>nd</sup>")


    x = x.replace(" 5 m ", " 5m ")
    x = x.replace(" 5 m.", " 5m.")
    if x.lower() == "temperature":
        return "temperature"
    # make CO2 subscript
    x = x.replace("CO2", "CO<sub>2</sub>")
    # fix O_2
    x = x.replace("O_2", "O<sub>2</sub>")
    x = x.replace(" ph ", " pH ")
    x = x.replace(" R2 ", " R<sup>2</sup> ")
    x = x.replace(" R2.", " R<sup>2</sup>.")
    # fix g/kg
    x = x.replace("g/kg", "g kg<sup>-1</sup>")

    # get rid of double spaces
    x = x.replace("  ", " ")
    # use regex to ensure any numbers have commas
    if "**Figure" in x:
        # ensure the sentence ends with .
        if x[-1] != ".":
            x = x + "."
    if "**Table" in x:
        # ensure the sentence ends with .
        if x[-1] != ".":
            x = x + "."
    # ensure there are spaces after commas, using regex
    x = re.sub(r",(\w)", r", \1", x)


    x = x.replace(" .", ".")
    x = x.replace(" ,", ",")
    x = x.replace(" :", ":")
    x = x.replace(" ;", ";")
    x = x.replace(" %", "%")
    # /m^3
    x = x.replace("/m3", "m<sup>-3</sup>")
    x = x.replace("/m^3", "m<sup>-3</sup>")
    # handle /m^2
    x = x.replace("/m2", "m<sup>-2</sup>")
    x = x.replace("/m^2", "m<sup>-2</sup>")
    # handl m-3
    x = x.replace(" m-3", " m<sup>-3</sup>")
    # fix /yr
    x = x.replace("/yr", "year<sup>-1</sup>")
    # fix /day
    x = x.replace("/day", "day<sup>-1</sup>")

    if number:
        if "year" not in x.lower():
            if "period" not in x.lower():
                # do not use numbers between brackets ()
                x = re.sub(r"(\d{1,3})(\d{3})", r"\1,\2", x)
    # identify any urls in the text and convert to markdown links
    url_pattern = re.compile(r'(https?://\S+)')
    urls = url_pattern.findall(x)
    for url in urls:
        x = x.replace(url, f"[{url}]({url})")


    return md_markdown(x)




