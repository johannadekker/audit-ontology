"""
TEMPORARY: LaTeX inspection tables for vereisten, maatregelen and hulpmiddelen.

Based on inspect_algoritmekader_v2.py (same table style). Differences:
  - runs for every collection whose CSV exists in output/
  - drops columns with at most one unique value from all tables
  - writes to output/latex/tmp_latex_tables/ so v2_latex_tables is untouched

Needs the CSVs written by the miner: <collection>.csv and
<collection>_vocabulary_counts.csv.
"""

from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "output"
LATEX_DIR = OUTPUT_DIR / "latex" / "tmp_latex_tables"
LATEX_DIR.mkdir(parents=True, exist_ok=True)

# collection name -> name of its id column in the CSV
COLLECTIONS = {
    "vereisten": "requirement_id",
    "maatregelen": "maatregel_id",
    "hulpmiddelen": "hulpmiddel_id",
}

# Classification by inspection of the source (not inferred from the data).
# Fields not listed here show up as "Unclassified" and are printed as a warning.
FIELD_TYPES = {
    "requirement_id": "Identity",
    "title": "Human-readable label",
    "source_file": "Source / provenance",
    "toelichting": "Descriptive text",
    "levenscyclus": "Controlled vocabulary",
    "onderwerp": "Controlled vocabulary",
    "soort-toepassing": "Applicability vocabulary",
    "risicogroep": "Applicability vocabulary",
    "rol-ai-act": "Applicability vocabulary",
    "transparantieverplichting": "Applicability vocabulary",
    "systeemrisico": "Applicability vocabulary",
    "rollen": "Organisational role vocabulary",
    "section_vereiste": "Normative statement",
    "section_toelichting": "Explanatory text",
    "section_bronnen": "Source / provenance",
    "section_van toepassing op": "Applicability note",
    "section_risico": "Risk description",
    "section_maatregelen": "Relationship placeholder",
    "section_hulpmiddelen": "Relationship placeholder",
    "uitzondering": "Applicability rule",
    "status_vereiste": "Status metadata",
}


# ---------------------------------------------------------
# LATEX FORMATTING
# ---------------------------------------------------------

ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def latex_escape(value):
    if pd.isna(value):
        return ""
    return "".join(ESCAPES.get(char, char) for char in str(value))


def latex_code(value):
    """Monospace text that may break after underscores and hyphens."""
    text = latex_escape(value)
    text = text.replace(r"\_", r"\_\allowbreak{}").replace("-", r"-\allowbreak{}")
    return r"\texttt{" + text + "}"


def export_latex_table(
    dataframe,
    filename,
    caption,
    label,
    column_format,
    code_columns=(),
    float_columns=(),
    font_size="small",
    tabcolsep=4,
    arraystretch=1.1,
):
    """Write a longtable. Code columns are monospace, float columns get one decimal."""
    latex_df = dataframe.copy()

    for column in latex_df.columns:
        if column in code_columns:
            latex_df[column] = latex_df[column].map(latex_code)
        elif column in float_columns:
            latex_df[column] = latex_df[column].map(lambda v: f"{float(v):.1f}")
        else:
            latex_df[column] = latex_df[column].map(latex_escape)

    latex_df.columns = [latex_escape(column) for column in latex_df.columns]

    # escape=False because the cells are already escaped above
    latex = latex_df.to_latex(
        index=False,
        escape=False,
        longtable=True,
        caption=caption,
        label=label,
        column_format=column_format,
    )

    (LATEX_DIR / filename).write_text(
        "\\begingroup\n"
        f"\\{font_size}\n"
        f"\\setlength{{\\tabcolsep}}{{{tabcolsep}pt}}\n"
        f"\\renewcommand{{\\arraystretch}}{{{arraystretch}}}\n"
        "\\setlength{\\LTcapwidth}{\\textwidth}\n"
        "\\setlength{\\LTleft}{0pt plus 1fill}\n"
        "\\setlength{\\LTright}{0pt plus 1fill}\n"
        f"{latex}"
        "\\endgroup\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------
# ONE COLLECTION
# ---------------------------------------------------------

CENTRE = r">{\centering\arraybackslash}p{%s\textwidth}"


def inspect_collection(name, id_column):
    csv_path = OUTPUT_DIR / f"{name}.csv"
    vocab_path = OUTPUT_DIR / f"{name}_vocabulary_counts.csv"

    if not csv_path.exists() or not vocab_path.exists():
        print(f"\n{name}: skipped, {csv_path.name} or {vocab_path.name} not found.")
        return

    df = pd.read_csv(csv_path)
    vocabulary_df = pd.read_csv(vocab_path)

    for required in (id_column, "source_file"):
        if required not in df.columns:
            raise ValueError(f"{csv_path.name} has no column '{required}'.")

    # Number of individual vocabulary values for list fields, otherwise the
    # number of distinct cell values.
    vocab_unique = vocabulary_df.groupby("field")["value"].nunique().to_dict()

    def unique_count(column):
        if column in vocab_unique:
            return int(vocab_unique[column])
        return int(df[column].nunique(dropna=True))

    # Columns with at most one unique value carry no information for the
    # tables (this includes the provenance columns).
    constant = [column for column in df.columns if unique_count(column) <= 1]
    print(f"\n=== {name}: {len(df)} rows ===")
    print(f"Dropped (<= 1 unique value): {constant}")

    df = df.drop(columns=constant)
    vocabulary_df = vocabulary_df[vocabulary_df["field"].isin(df.columns)]

    prefix = f"algoritmekader_{name}"
    tag = f"tab:algoritmekader-{name}"

    # 1. Column profile
    profile_df = pd.DataFrame({
        "Column": df.columns,
        "Non-empty": [int(df[c].notna().sum()) for c in df.columns],
        "Total rows": len(df),
        "Unique values": [unique_count(c) for c in df.columns],
        "Coverage (%)": [round(df[c].notna().mean() * 100, 1) for c in df.columns],
    })

    export_latex_table(
        profile_df,
        f"{prefix}_column_profile.tex",
        caption=f"Profile of fields extracted from Algoritmekader {name}.",
        label=f"{tag}-column-profile",
        column_format=(
            r"P{0.40\textwidth}"
            + CENTRE % "0.10" + CENTRE % "0.10" + CENTRE % "0.12" + CENTRE % "0.12"
        ),
        code_columns={"Column"},
        float_columns={"Coverage (%)"},
        font_size="footnotesize",
        tabcolsep=3,
        arraystretch=1.10,
    )

    # 2. Metadata fields and Markdown sections
    metadata_columns = [c for c in df.columns if not c.startswith("section_")]
    section_columns = [c for c in df.columns if c.startswith("section_")]

    export_latex_table(
        pd.DataFrame({"Metadata field": metadata_columns}),
        f"{prefix}_metadata_fields.tex",
        caption=f"Metadata fields present in Algoritmekader {name} files.",
        label=f"{tag}-metadata-fields",
        column_format=r"P{0.72\textwidth}",
        code_columns={"Metadata field"},
        arraystretch=1.08,
    )

    export_latex_table(
        pd.DataFrame({"Markdown section": section_columns}),
        f"{prefix}_section_fields.tex",
        caption=f"Markdown sections identified in Algoritmekader {name} files.",
        label=f"{tag}-section-fields",
        column_format=r"P{0.90\textwidth}",
        code_columns={"Markdown section"},
    )

    # 3. Controlled vocabularies (counts come from the miner's exploded output)
    vocabulary_table = vocabulary_df.rename(columns={
        "field": "Field",
        "value": "Value",
        "count": "Count",
        "coverage_pct": "Coverage (%)",
    })

    export_latex_table(
        vocabulary_table,
        f"{prefix}_controlled_values.tex",
        caption=f"Individual controlled vocabulary values observed in the Algoritmekader {name}.",
        label=f"{tag}-controlled-values",
        column_format=(
            r"P{0.27\textwidth}P{0.50\textwidth}" + CENTRE % "0.07" + CENTRE % "0.10"
        ),
        code_columns={"Field"},
        float_columns={"Coverage (%)"},
        font_size="footnotesize",
        tabcolsep=3,
        arraystretch=1.08,
    )

    # 4. Semantic classification of the columns
    classification_df = pd.DataFrame({
        "Field": df.columns,
        "Category": [FIELD_TYPES.get(c, "Unclassified") for c in df.columns],
        "Non-empty": [int(df[c].notna().sum()) for c in df.columns],
        "Unique values": [unique_count(c) for c in df.columns],
    })

    unclassified = classification_df.loc[
        classification_df["Category"] == "Unclassified", "Field"
    ]
    if not unclassified.empty:
        print(f"WARNING: unclassified fields, add them to FIELD_TYPES: {list(unclassified)}")

    export_latex_table(
        classification_df,
        f"{prefix}_field_classification.tex",
        caption=f"Structural classification of fields extracted from the Algoritmekader {name}.",
        label=f"{tag}-field-classification",
        column_format=(
            r"P{0.38\textwidth}P{0.30\textwidth}" + CENTRE % "0.10" + CENTRE % "0.12"
        ),
        code_columns={"Field"},
        font_size="footnotesize",
        tabcolsep=3,
        arraystretch=1.08,
    )


# ---------------------------------------------------------
# RUN
# ---------------------------------------------------------

for collection, id_col in COLLECTIONS.items():
    inspect_collection(collection, id_col)

print(f"\nLaTeX tables written to: {LATEX_DIR}")
print("Preamble needs: fontenc T1, booktabs, longtable, array, ragged2e and")
print(r"\newcolumntype{P}[1]{>{\RaggedRight\arraybackslash}p{#1}}")
