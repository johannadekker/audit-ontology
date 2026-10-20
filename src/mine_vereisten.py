from datetime import datetime
from pathlib import Path
import json
import re
import subprocess
import pandas as pd
import yaml


# ---------------------------------------------------------
# PATHS
# ---------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALGORITMEKADER_REPO_ROOT = PROJECT_ROOT / "data" / "Algoritmekader"

VEREISTEN_DIR = (
    PROJECT_ROOT
    / "data"
    / "Algoritmekader"
    / "docs"
    / "voldoen-aan-wetten-en-regels"
    / "vereisten"
)

OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Columns added for source-snapshot provenance rather than mined from the
# Markdown files. Kept separate from the requirement schema so they don't
# get treated as requirement fields during profiling/classification.
PROVENANCE_COLUMNS = {
    "repo_commit_hash",
    "repo_commit_date",
    "extraction_run_at",
}

# Heading-parsing artefacts that are not meaningful as separate columns 
# but should be merged into the explanatory text.
# !! Note that "begrippen" contains a list of terms !!
# !! aia-38 contains applicability conditions
MERGE_INTO_TOELICHTING = {
    #aia-38
    "testen van ai-systemen met een hoog risico onder reële omstandigheden buiten ai-testomgevingen",
    #aia-06
    "voorbeeld",
    #avg-10
    "algemene eisen aan geautomatiseerde besluitvorming door overheden",
    "begrippen",
}

# ---------------------------------------------------------
# CLEANING / MODELLING CONFIGURATION
# ---------------------------------------------------------

# index.md is an overview page, not an individual requirement.
EXCLUDED_FILES = {
    "index.md",
}
# Extract the unique requirement ID from the standardized Algoritmekader URN format
ID_PREFIX = "urn:nl:ak:ver:"


def extract_requirement_id(raw_id: str, source_file: Path) -> str:
    """
    Convert an Algoritmekader requirement URN such as
    'urn:nl:ak:ver:aia-00' to the compact requirement ID 'aia-00'.
    """
    if not isinstance(raw_id, str) or not raw_id.startswith(ID_PREFIX):
        raise ValueError(
            f"Unexpected or missing requirement ID in {source_file.name}: {raw_id!r}"
        )

    return raw_id.removeprefix(ID_PREFIX)

# These fields are website metadata and are not relevant to the requirement table.
DROP_FIELDS = {
    "hide",
    "open-source",
    "allow_excel_export",
    "summary",
}

# Controlled/multi-valued metadata to inspect as vocabularies. These values are
# counted individually rather than as serialized list combinations.
VOCABULARY_FIELDS = [
    "levenscyclus",
    "onderwerp",
    "soort-toepassing",
    "risicogroep",
    "rol-ai-act",
    "transparantieverplichting",
    "systeemrisico",
    "rollen",
    "status_vereiste",
]


# ---------------------------------------------------------
# PARSING FUNCTIONS
# ---------------------------------------------------------

def get_repo_snapshot(repo_path: Path) -> dict:
    """
    Capture the commit hash and commit date of the Algoritmekader source
    repository at extraction time, so every exported dataset carries its
    own provenance record rather than relying on a note written by hand
    after the fact.
 
    Falls back to (None, None) with a warning if the path is not a git
    checkout or git is unavailable, so a broken snapshot never silently
    stops the extraction.
    """
    try:
        commit_hash = subprocess.check_output(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
 
        commit_date = subprocess.check_output(
            ["git", "-C", str(repo_path), "log", "-1", "--format=%cd", "--date=iso"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
 
    except (subprocess.CalledProcessError, FileNotFoundError) as error:
        print(f"WARNING: could not read git metadata from {repo_path}: {error}")
        commit_hash, commit_date = None, None
 
    return {
        "commit_hash": commit_hash,
        "commit_date": commit_date,
    }

def split_frontmatter(text):
    """
    Split a Markdown file into YAML frontmatter and Markdown body.

    Frontmatter must be enclosed by '---' lines at the start of the file.
    """

    pattern = re.compile(
        r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z",
        flags=re.DOTALL,
    )

    match = pattern.match(text)

    if not match:
        return {}, text.strip()

    metadata_text, body = match.groups()
    metadata = yaml.safe_load(metadata_text) or {}

    return metadata, body.strip()


    # Normalise an H2 Markdown heading for use as a dataframe field name
    # Consistent naming allows avoids duplicates and allows for application of 
    # complex parsing logic
def normalise_heading(heading):

    # Remove MkDocs attributes such as: { data-search-exclude }
    heading = re.sub(r"\s*\{.*?\}\s*$", "", heading).strip()

    # Collapse repeated whitespace/newlines and use lowercase consistently.
    heading = re.sub(r"\s+", " ", heading)
    # Lowercase for consistent column names
    return heading.lower()


# Add content to a section without losing earlier content if the same heading
# occurs more than once.
def append_section(sections, section_name, content):
    if not content:
        return

    if section_name in sections and sections[section_name]:
        sections[section_name] = (
            sections[section_name].rstrip()
            + "\n\n"
            + content.lstrip()
        )
    else:
        sections[section_name] = content.strip()


#Extract Markdown H2 sections and their content into a dictionary keyed by normalised heading.
def extract_sections(body):
    pattern = re.compile(
        r"^##\s+(.+?)\s*$",
        flags=re.MULTILINE,
    )

    matches = list(pattern.finditer(body))
    sections = {}

    for i, match in enumerate(matches):
        original_heading = re.sub(
            r"\s*\{.*?\}\s*$",
            "",
            match.group(1),
        ).strip()

        heading = normalise_heading(match.group(1))

        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[start:end].strip()

        if heading in MERGE_INTO_TOELICHTING:
            # Preserve the informative heading as part of the explanatory text
            # so no source meaning is lost when the extra column is removed.
            merged_content = f"{original_heading}\n\n{content}".strip()
            append_section(sections, "toelichting", merged_content)
        else:
            append_section(sections, heading, content)

    return sections


#Extract Markdown links of the form [anchor text](target) 
# into a list of dictionaries with 'text' and 'target' keys.
def extract_links(text):
    pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    return [
        {
            "text": anchor.strip(),
            "target": target.strip(),
        }
        for anchor, target in pattern.findall(text)
    ]


#Convert a nested Python value to a JSON string for CSV/Excel export
def make_export_safe(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)

    return value

# Return metadata as individual values for vocabulary analysis.
# YAML lists remain separate values. Scalar values are treated as a
# one-element list. Missing values return an empty list.
def as_individual_values(value):
    if value is None:
        return []

    # pandas may represent missing scalar values as NaN.
    if not isinstance(value, (list, dict)) and pd.isna(value):
        return []

    if isinstance(value, list):
        return value

    return [value]

#Convert a vocabulary item to a stable printable representation.
def vocabulary_value_to_string(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    return str(value)


# ---------------------------------------------------------
# MINE ALL REQUIREMENT FILES
# ---------------------------------------------------------

records = []
links = []

all_files = sorted(VEREISTEN_DIR.glob("*.md"))
files = [
    filepath 
    for filepath in all_files 
    if filepath.name not in EXCLUDED_FILES
]

print(f"Found {len(all_files)} Markdown files.")
print(f"Excluded {len(all_files) - len(files)} non-requirement file(s).")
print(f"Processing {len(files)} requirement files.")

for filepath in files:
    text = filepath.read_text(encoding="utf-8")

    metadata, body = split_frontmatter(text)
    sections = extract_sections(body)

    #Extract requirement id from Algoritmekader URN.
    raw_id = metadata.get("id")
    requirement_id = extract_requirement_id(raw_id, filepath)

    #Record started with clean structure.
    record = {
        "requirement_id": requirement_id,
        "title": metadata.get("title"),
        "filename": filepath.name,
    }

    #Copy YAML metadata fields into the record, except for those that are already
    #explicitly handled or are known to be irrelevant for the requirement table.
    for key, value in metadata.items():
        if key in {
            "id",
            "title",
            "slug",
            "hide",
            "open-source",
            "allow_excel_export",
            "summary",
        }:
            continue

        record[key] = value

    # Put Markdown H2 sections in their own columns.
    for section_name, content in sections.items():
        record[f"section_{section_name}"] = content

    # Drop known web-rendering artefacts and content that belongs in separate
    # source datasets rather than in the requirement table.
    for field in DROP_FIELDS:
        record.pop(field, None)

    records.append(record)

    # Extract every explicit Markdown link for a separate relationship table.

    # Extract every explicit Markdown link into a separate relationship table.
    links.extend(
        {
            "requirement_id": requirement_id,
            "source_file": filepath.name,
            "anchor_text": link["text"],
            "target": link["target"],
        }
        for link in extract_links(body)
    )


# ---------------------------------------------------------
# DATAFRAMES
# ---------------------------------------------------------

df = pd.DataFrame(records)
links_df = pd.DataFrame(links)

# Defensive cleanup in case a dropped field exists only in some records.
df = df.drop(columns=list(DROP_FIELDS), errors="ignore")


# ---------------------------------------------------------
# SOURCE-SNAPSHOT PROVENANCE
# ---------------------------------------------------------
 
# Record which exact commit of the Algoritmekader repository this run was
# extracted from, and when the extraction itself was run. The repo can move
# on after this run, so both timestamps matter: the commit date says how
# current the *source* was, and extraction_run_at says when this dataset
# was produced from it.
repo_snapshot = get_repo_snapshot(ALGORITMEKADER_REPO_ROOT)
 
df["repo_commit_hash"] = repo_snapshot["commit_hash"]
df["repo_commit_date"] = repo_snapshot["commit_date"]
df["extraction_run_at"] = datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------
# EXPLODE CONTROLLED VOCABULARIES
# ---------------------------------------------------------

# Detailed relational data for each individual vocabulary value

vocabulary_assignments = []

for _, row in df.iterrows():
    for field in VOCABULARY_FIELDS:
        if field not in df.columns:
            continue

        for value in as_individual_values(row.get(field)):
            vocabulary_assignments.append(
                {
                    "requirement_id": row.get("requirement_id"),
                    "source_file": row.get("source_file"),
                    "field": field,
                    "value": vocabulary_value_to_string(value),
                }
            )

vocabulary_assignments_df = pd.DataFrame(vocabulary_assignments)

if vocabulary_assignments_df.empty:
    vocabulary_counts_df = pd.DataFrame(
        columns=["field", "value", "count", "coverage_pct"]
    )
else:
    vocabulary_counts_df = (
        vocabulary_assignments_df
        .groupby(["field", "value"], as_index=False)
        .agg(count=("requirement_id", "nunique"))
    )
    # Creates a descriptive overview summary
    
    vocabulary_counts_df["coverage_pct"] = (
        vocabulary_counts_df["count"] / len(df) * 100
    ).round(2)

    vocabulary_counts_df = vocabulary_counts_df.sort_values(
        ["field", "count", "value"],
        ascending=[True, False, True],
    ).reset_index(drop=True)


# ---------------------------------------------------------
# COLUMN PROFILE AFTER CLEANING
# ---------------------------------------------------------


def count_unique_values(column):
    """
    Count semantic values rather than serialized list combinations
    for controlled/multi-valued fields.
    """
    if column in VOCABULARY_FIELDS:
        return int(
            vocabulary_assignments_df.loc[
                vocabulary_assignments_df["field"] == column,
                "value",
            ].nunique()
        )

    return int(
        df[column]
        .map(make_export_safe)
        .nunique(dropna=True)
    )


column_profile_df = pd.DataFrame(
    [
        {
            "column": column,
            "non_empty": int(df[column].notna().sum()),
            "total_rows": len(df),
            "unique_values": count_unique_values(column),
            "coverage_pct": round(
                df[column].notna().mean() * 100,
                1,
            ),
        }
        for column in df.columns
        if column not in PROVENANCE_COLUMNS
    ]
)


# ---------------------------------------------------------
# EXPORT
# ---------------------------------------------------------

# Keep Python lists intact until all analysis/counting is complete. Only now
# serialize them for flat-file output.
export_df = df.map(make_export_safe)

excel_path = OUTPUT_DIR / "vereisten.xlsx"

with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
    export_df.to_excel(
        writer,
        sheet_name="vereisten",
        index=False,
    )

    links_df.to_excel(
        writer,
        sheet_name="links",
        index=False,
    )

    vocabulary_assignments_df.to_excel(
        writer,
        sheet_name="vocabulary_assignments",
        index=False,
    )

    vocabulary_counts_df.to_excel(
        writer,
        sheet_name="vocabulary_counts",
        index=False,
    )

    column_profile_df.to_excel(
        writer,
        sheet_name="column_profile",
        index=False,
    )


csv_path = OUTPUT_DIR / "vereisten.csv"
vocabulary_counts_path = OUTPUT_DIR / "vereisten_vocabulary_counts.csv"
vocabulary_assignments_path = OUTPUT_DIR / "vereisten_vocabulary_assignments.csv"

export_df.to_csv(
    csv_path,
    index=False,
    encoding="utf-8-sig",
)

vocabulary_counts_df.to_csv(
    vocabulary_counts_path,
    index=False,
    encoding="utf-8-sig",
)

vocabulary_assignments_df.to_csv(
    vocabulary_assignments_path,
    index=False,
    encoding="utf-8-sig",
)


# ---------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------

print()
print("Done.")
print(f"Source commit: {repo_snapshot['commit_hash']} ({repo_snapshot['commit_date']})")
print(f"Extraction run at: {df['extraction_run_at'].iloc[0]}")
print(f"Requirements: {len(df)}")
print(f"Excel: {excel_path}")
print(f"CSV: {csv_path}")
print(f"Vocabulary counts: {vocabulary_counts_path}")
print(f"Vocabulary assignments: {vocabulary_assignments_path}")

print()
print("Columns retained:")
for column in df.columns:
    print(f" - {column}")

print()
print("Vocabulary values found:")
for field in VOCABULARY_FIELDS:
    if vocabulary_counts_df.empty:
        break

    subset = vocabulary_counts_df[vocabulary_counts_df["field"] == field]

    if subset.empty:
        continue

    print(f"\n{field} ({len(subset)} individual values)")
    for _, vocab_row in subset.iterrows():
        print(
            f" - {vocab_row['value']}: "
            f"{int(vocab_row['count'])} requirements "
            f"({vocab_row['coverage_pct']}%)"
        )
