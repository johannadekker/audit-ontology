from pathlib import Path

import pandas as pd


# ---------------------------------------------------------
# PATHS
# ---------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]

INPUT_FILE = PROJECT_ROOT / "output" / "vereisten.csv"
VOCAB_COUNTS_FILE = PROJECT_ROOT / "output" / "vereisten_vocabulary_counts.csv"

LATEX_DIR = PROJECT_ROOT / "output" / "latex" / "v2_latex_tables"
LATEX_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------
# V2 SCHEMA
# ---------------------------------------------------------

# These fields identify the cleaned V2 dataset. If old V1/intermediate identity
# columns reappear, stop rather than silently generating misleading V2 tables.
REQUIRED_V2_COLUMNS = {
    "requirement_id",
    "title",
    "source_file",
    "section_vereiste",
    "section_toelichting",
}

FORBIDDEN_OLD_IDENTITY_COLUMNS = {
    "filename",
    "slug",
    "id",
}


# ---------------------------------------------------------
# LATEX FORMATTING
# ---------------------------------------------------------

def latex_escape(value):
    """Escape ordinary text for LaTeX."""
    if pd.isna(value):
        return ""

    text = str(value)

    replacements = {
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

    return "".join(replacements.get(char, char) for char in text)


def latex_code(value):
    """
    Format identifiers and vocabulary values as breakable monospace text.

    Break opportunities are inserted after underscores and hyphens, matching
    the formatting used in the finalized V1 tables.
    """
    if pd.isna(value):
        return ""

    text = str(value)
    output = []

    for char in text:
        if char == "_":
            output.append(r"\_\allowbreak{}")
        elif char == "-":
            output.append(r"-\allowbreak{}")
        elif char == "\\":
            output.append(r"\textbackslash{}")
        elif char == "&":
            output.append(r"\&")
        elif char == "%":
            output.append(r"\%")
        elif char == "$":
            output.append(r"\$")
        elif char == "#":
            output.append(r"\#")
        elif char == "{":
            output.append(r"\{")
        elif char == "}":
            output.append(r"\}")
        elif char == "~":
            output.append(r"\textasciitilde{}")
        elif char == "^":
            output.append(r"\textasciicircum{}")
        else:
            output.append(char)

    return r"\texttt{" + "".join(output) + "}"


def format_number(value):
    """Render integer-like values without a decimal suffix."""
    if pd.isna(value):
        return ""

    if isinstance(value, float) and value.is_integer():
        return str(int(value))

    return str(value)


def export_latex_table(
    dataframe,
    filename,
    caption,
    label,
    column_format,
    *,
    code_columns=None,
    float_columns=None,
    font_size="small",
    tabcolsep=4,
    arraystretch=1.1,
):
    """
    Export a consistently formatted longtable for inclusion in the thesis.

    code_columns are rendered as breakable monospace identifiers.
    float_columns are rendered to one decimal place.
    All other textual cells are LaTeX-escaped normally.
    """

    code_columns = set(code_columns or [])
    float_columns = set(float_columns or [])

    latex_df = dataframe.copy()

    for column in latex_df.columns:
        if column in code_columns:
            latex_df[column] = latex_df[column].map(latex_code)

        elif column in float_columns:
            latex_df[column] = latex_df[column].map(
                lambda value: ""
                if pd.isna(value)
                else f"{float(value):.1f}"
            )

        else:
            latex_df[column] = latex_df[column].map(
                lambda value: (
                    ""
                    if pd.isna(value)
                    else format_number(value)
                    if isinstance(value, (int, float))
                    else latex_escape(value)
                )
            )

    # escape=False is intentional: cells have already been escaped/formatted.
    latex_df.columns = [
        latex_escape(column)
        for column in latex_df.columns
    ]

    latex = latex_df.to_latex(
        index=False,
        escape=False,
        longtable=True,
        caption=caption,
        label=label,
        column_format=column_format,
    )

    formatted_latex = (
        "\\begingroup\n"
        f"\\{font_size}\n"
        f"\\setlength{{\\tabcolsep}}{{{tabcolsep}pt}}\n"
        f"\\renewcommand{{\\arraystretch}}{{{arraystretch}}}\n"
        "\\setlength{\\LTcapwidth}{\\textwidth}\n"
        "\\setlength{\\LTleft}{0pt plus 1fill}\n"
        "\\setlength{\\LTright}{0pt plus 1fill}\n"
        f"{latex}"
        "\\endgroup\n"
    )

    output_path = LATEX_DIR / filename
    output_path.write_text(formatted_latex, encoding="utf-8")


# ---------------------------------------------------------
# LOAD AND VALIDATE DATA
# ---------------------------------------------------------

if not INPUT_FILE.exists():
    raise FileNotFoundError(f"Could not find: {INPUT_FILE}")

if not VOCAB_COUNTS_FILE.exists():
    raise FileNotFoundError(
        f"Could not find: {VOCAB_COUNTS_FILE}\n"
        "Run mine_vereisten.py first so the exploded vocabulary counts are generated."
    )

df = pd.read_csv(INPUT_FILE)
vocabulary_df = pd.read_csv(VOCAB_COUNTS_FILE)

missing_v2_columns = REQUIRED_V2_COLUMNS - set(df.columns)
old_identity_columns = FORBIDDEN_OLD_IDENTITY_COLUMNS & set(df.columns)

if missing_v2_columns:
    raise ValueError(
        "The requirements CSV does not match the expected final V2 schema. "
        f"Missing columns: {sorted(missing_v2_columns)}"
    )

if old_identity_columns:
    raise ValueError(
        "The requirements CSV appears to be an older intermediate export. "
        f"Unexpected V1 identity columns found: {sorted(old_identity_columns)}. "
        "Regenerate vereisten.csv with the final V2 mining script before "
        "generating LaTeX."
    )

required_vocab_columns = {"field", "value", "count", "coverage_pct"}
missing_vocab_columns = required_vocab_columns - set(vocabulary_df.columns)

if missing_vocab_columns:
    raise ValueError(
        "The vocabulary-count CSV has an unexpected schema. "
        f"Missing columns: {sorted(missing_vocab_columns)}"
    )


# For list-valued vocabulary fields, df.nunique() counts distinct serialized
# list combinations. Use the exploded vocabulary output to report the number
# of individual semantic values instead.
vocab_unique_counts = (
    vocabulary_df.groupby("field")["value"]
    .nunique()
    .to_dict()
)


def semantic_unique_count(column):
    """Return distinct vocabulary terms where available, otherwise cell values."""
    if column in vocab_unique_counts:
        return int(vocab_unique_counts[column])

    return int(df[column].nunique(dropna=True))


print(f"Rows: {len(df)}")
print(f"Columns: {len(df.columns)}")


# ---------------------------------------------------------
# 1. COLUMN PROFILE
# ---------------------------------------------------------

profile_rows = []

for column in df.columns:
    non_empty = int(df[column].notna().sum())
    unique = semantic_unique_count(column)

    profile_rows.append({
        "Column": column,
        "Non-empty": non_empty,
        "Total rows": len(df),
        "Unique values": unique,
        "Coverage (%)": round((non_empty / len(df)) * 100, 1),
    })

profile_df = pd.DataFrame(profile_rows)

print("\nCOLUMN PROFILE")
print("=" * 80)
print(profile_df.to_string(index=False))

export_latex_table(
    profile_df,
    "algoritmekader_column_profile.tex",
    caption="Profile of fields extracted from Algoritmekader requirements.",
    label="tab:algoritmekader-column-profile",
    column_format=(
        r"P{0.40\textwidth}"
        r">{\centering\arraybackslash}p{0.10\textwidth}"
        r">{\centering\arraybackslash}p{0.10\textwidth}"
        r">{\centering\arraybackslash}p{0.12\textwidth}"
        r">{\centering\arraybackslash}p{0.12\textwidth}"
    ),
    code_columns={"Column"},
    float_columns={"Coverage (%)"},
    font_size="footnotesize",
    tabcolsep=3,
    arraystretch=1.10,
)


# ---------------------------------------------------------
# 2. SEPARATE METADATA FROM MARKDOWN SECTIONS
# ---------------------------------------------------------

metadata_columns = [
    column
    for column in df.columns
    if not column.startswith("section_")
]

section_columns = [
    column
    for column in df.columns
    if column.startswith("section_")
]

metadata_df = pd.DataFrame({
    "Metadata field": metadata_columns
})

sections_df = pd.DataFrame({
    "Markdown section": section_columns
})

export_latex_table(
    metadata_df,
    "algoritmekader_metadata_fields.tex",
    caption="Metadata fields present in Algoritmekader requirement files.",
    label="tab:algoritmekader-metadata-fields",
    column_format=r"P{0.72\textwidth}",
    code_columns={"Metadata field"},
    font_size="small",
    arraystretch=1.08,
)

export_latex_table(
    sections_df,
    "algoritmekader_section_fields.tex",
    caption="Markdown sections identified in Algoritmekader requirement files.",
    label="tab:algoritmekader-section-fields",
    column_format=r"P{0.90\textwidth}",
    code_columns={"Markdown section"},
    font_size="small",
    arraystretch=1.10,
)


# ---------------------------------------------------------
# 3. CONTROLLED VOCABULARIES
# ---------------------------------------------------------

# The mining script has already exploded list-valued YAML fields into
# individual vocabulary assignments. Use those counts directly rather than
# treating serialized list combinations as single categorical values.

vocabulary_df = vocabulary_df.rename(columns={
    "field": "Field",
    "value": "Value",
    "count": "Count",
    "coverage_pct": "Coverage (%)",
})

vocabulary_df["Coverage (%)"] = (
    vocabulary_df["Coverage (%)"]
    .astype(float)
    .round(1)
)

print("\nCONTROLLED VOCABULARIES")
print("=" * 80)
print(vocabulary_df.to_string(index=False))

export_latex_table(
    vocabulary_df,
    "algoritmekader_controlled_values.tex",
    caption=(
        "Individual controlled vocabulary values observed "
        "in the Algoritmekader requirements."
    ),
    label="tab:algoritmekader-controlled-values",
    column_format=(
        r"P{0.30\textwidth}"
        r"P{0.47\textwidth}"
        r">{\centering\arraybackslash}p{0.07\textwidth}"
        r">{\centering\arraybackslash}p{0.10\textwidth}"
    ),
    code_columns={"Field"},
    float_columns={"Coverage (%)"},
    font_size="footnotesize",
    tabcolsep=3,
    arraystretch=1.08,
)


# ---------------------------------------------------------
# 4. CLASSIFY COLUMNS SEMANTICALLY
# ---------------------------------------------------------

# These classifications are based on inspection of the Algoritmekader source,
# not inferred from the number of unique values.

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

classification_rows = []
unclassified_columns = []

for column in df.columns:
    non_empty = int(df[column].notna().sum())
    unique = semantic_unique_count(column)

    category = FIELD_TYPES.get(column, "Unclassified")

    if category == "Unclassified":
        unclassified_columns.append(column)

    classification_rows.append({
        "Field": column,
        "Category": category,
        "Non-empty": non_empty,
        "Unique values": unique,
    })

classification_df = pd.DataFrame(classification_rows)

print("\nFIELD CLASSIFICATION")
print("=" * 80)
print(classification_df.to_string(index=False))

if unclassified_columns:
    print("\nWARNING: New/unclassified fields found:")
    for column in unclassified_columns:
        print(f" - {column}")
    print("Add these fields to FIELD_TYPES after inspecting their semantics.")

export_latex_table(
    classification_df,
    "algoritmekader_field_classification.tex",
    caption=(
        "Structural classification of fields extracted "
        "from the Algoritmekader."
    ),
    label="tab:algoritmekader-field-classification",
    column_format=(
        r"P{0.38\textwidth}"
        r"P{0.30\textwidth}"
        r">{\centering\arraybackslash}p{0.10\textwidth}"
        r">{\centering\arraybackslash}p{0.12\textwidth}"
    ),
    code_columns={"Field"},
    font_size="footnotesize",
    tabcolsep=3,
    arraystretch=1.08,
)


# ---------------------------------------------------------
# DONE
# ---------------------------------------------------------

print("\nLaTeX tables written to:")
print(LATEX_DIR)

for file in sorted(LATEX_DIR.glob("*.tex")):
    print(" -", file.name)

print("\nLaTeX preamble requirements:")
print(r" - \usepackage[T1]{fontenc}")
print(r" - \usepackage{booktabs}")
print(r" - \usepackage{longtable}")
print(r" - \usepackage{array}")
print(r" - \usepackage{ragged2e}")
print(r" - \newcolumntype{P}[1]{>{\RaggedRight\arraybackslash}p{#1}}")
