"""
mine_vereisten.py

Extracts the Algoritmekader's Markdown sources into structured CSV/Excel
exports for the auditability-ontology thesis pipeline (V3 of the
data-preparation stage):

  - vereisten    (requirements)  docs/voldoen-aan-wetten-en-regels/vereisten/
  - maatregelen  (measures)      docs/voldoen-aan-wetten-en-regels/maatregelen/
  - hulpmiddelen (tools)         docs/voldoen-aan-wetten-en-regels/hulpmiddelen/
  - begrippenlijst (glossary)    docs/begrippenlijst.md

Pipeline: mine -> build dataframes -> attach provenance -> explode
vocabularies -> profile columns -> export. Each stage is a standalone
function (see PIPELINE section at the bottom); main() wires them together.

Usage:
    python mine_vereisten.py
    python mine_vereisten.py --only vereisten maatregelen
    python mine_vereisten.py --docs-dir /path/to/docs --output-dir /path/to/output

Dependencies: pandas, pyyaml, openpyxl.
Tests: see test_mine_vereisten.py (pytest) for the pure parsing functions.
"""

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional
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

DOCS_DIR = ALGORITMEKADER_REPO_ROOT / "docs"
RULES_DIR = DOCS_DIR / "voldoen-aan-wetten-en-regels"

BEGRIPPENLIJST_FILE = DOCS_DIR / "begrippenlijst.md"

OUTPUT_DIR = PROJECT_ROOT / "output"

# Columns added for source-snapshot provenance rather than mined from the
# Markdown files. Kept separate from the requirement schema so they don't
# get treated as requirement fields during profiling/classification.
PROVENANCE_COLUMNS = {
    "repo_commit_hash",
    "repo_commit_date",
    "repo_working_tree_dirty",
    "extraction_run_at",
}

# Heading-parsing artefacts that are not meaningful as separate columns
# but should be merged into the explanatory text.
# !! Note that "begrippen" contains a list of terms !!
MERGE_INTO_TOELICHTING = {
    # aia-38
    "testen van ai-systemen met een hoog risico onder reële omstandigheden buiten ai-testomgevingen",
    "voorbeeld",
    # avg-10
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

# Extract the unique requirement ID from the standardized Algoritmekader URN format.
ID_PREFIX = "urn:nl:ak:ver:"

# These fields are useful for rendering the Algoritmekader website, but are
# not part of the requirement data we want to model.
DROP_FIELDS = {
    "hide",
    "open-source",
    "allow_excel_export",
    "summary",
}

# YAML keys that are handled explicitly elsewhere (identity fields) or are
# known website-rendering artefacts, and so are skipped when copying the
# remaining front-matter keys into a record.
METADATA_KEYS_HANDLED_ELSEWHERE = {
    "id",
    "title",
    "slug",
    *DROP_FIELDS,
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

# Maatregelen follow a fixed template (Maatregel / Toelichting / Risico / ...),
# but authors add free-form H2 headings of their own (e.g. org-05 and org-15,
# or the 'Stap 1..5' headings). Left alone, each of those becomes a lone
# one-record column. With a standard-section list, every heading outside it
# is merged into 'toelichting' with the heading kept as text, the same way
# MERGE_INTO_TOELICHTING works for vereisten.
MAATREGELEN_STANDARD_SECTIONS = {
    "maatregel",
    "toelichting",
    "risico",
    "bijbehorende vereiste(n)",
    "bronnen",
    "voorbeelden",
}

# Template headings that occur in a slightly different spelling.
MAATREGELEN_SECTION_ALIASES = {
    "voorbeeld": "voorbeelden",
    "bijbehorende vereisten": "bijbehorende vereiste(n)",
}

# Per-collection mining configuration. The three collections share one file
# shape (YAML frontmatter + H2 sections) but differ in three ways:
#   - id_prefix: vereisten/maatregelen carry a URN id in the frontmatter.
#     Hulpmiddelen have NO id field, so id_prefix is None and the record id
#     is the file name stem (e.g. 'AIIA').
#   - merge_headings: the heading merge list above was derived from vereisten
#     only (aia-38, avg-10); applying it to the other collections would
#     silently fold their "Voorbeeld" sections into toelichting.
#   - vocabulary_fields: which metadata fields are controlled vocabularies.
COLLECTIONS = {
    "vereisten": {
        "subdir": "vereisten",
        "id_prefix": "urn:nl:ak:ver:",
        "id_column": "requirement_id",
        "merge_headings": MERGE_INTO_TOELICHTING,
        "vocabulary_fields": VOCABULARY_FIELDS,
    },
    "maatregelen": {
        "subdir": "maatregelen",
        "id_prefix": "urn:nl:ak:mtr:",
        "id_column": "maatregel_id",
        "merge_headings": set(),
        "standard_sections": MAATREGELEN_STANDARD_SECTIONS,
        "section_aliases": MAATREGELEN_SECTION_ALIASES,
        "vocabulary_fields": ["levenscyclus", "onderwerp", "rollen"],
    },
    "hulpmiddelen": {
        "subdir": "hulpmiddelen",
        "id_prefix": None,
        "id_column": "hulpmiddel_id",
        "merge_headings": set(),
        "vocabulary_fields": ["categorie", "levenscyclus", "onderwerp", "rollen"],
    },
}

# Frontmatter fields that point at other collections. Each value is a file
# name stem of the target collection (e.g. 'aia-00-verboden-AI-praktijken'),
# not a compact id, so they are resolved via source_file in
# build_cross_references().
CROSS_REFERENCE_FIELDS = {
    "vereiste": "vereisten",
    "maatregel": "maatregelen",
}


# ---------------------------------------------------------
# PROVENANCE
# ---------------------------------------------------------


def get_repo_snapshot(repo_path: Path) -> dict:
    """
    Capture the commit hash and commit date of the Algoritmekader source
    repository at extraction time, so every exported dataset carries its
    own provenance record rather than relying on a note written by hand
    after the fact.

    Also checks whether the working tree has uncommitted changes: the
    commit hash alone only describes the last *committed* state, so a
    dirty tree means the files actually mined may not match that commit.

    Falls back to (None, None, None) with a warning if the path is not a
    git checkout or git is unavailable, so a broken snapshot never
    silently stops the extraction.
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

        status_output = subprocess.check_output(
            ["git", "-C", str(repo_path), "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
        ).decode()

        is_dirty = bool(status_output.strip())

        if is_dirty:
            print(
                f"WARNING: {repo_path} has uncommitted changes at extraction "
                "time. The recorded commit hash reflects the last commit, "
                "not these local edits."
            )

    except (subprocess.CalledProcessError, FileNotFoundError) as error:
        print(f"WARNING: could not read git metadata from {repo_path}: {error}")
        commit_hash, commit_date, is_dirty = None, None, None

    return {
        "commit_hash": commit_hash,
        "commit_date": commit_date,
        "working_tree_dirty": is_dirty,
    }


# ---------------------------------------------------------
# PARSING HELPERS
# ---------------------------------------------------------


def extract_requirement_id(raw_id, source_file: Path, id_prefix: Optional[str]) -> str:
    """
    Convert a requirement URN such as 'urn:nl:ak:ver:aia-00' to the compact
    ID 'aia-00', given the URN prefix for this collection.

    id_prefix is a parameter rather than a hardcoded constant so this same
    function can mine the other collections: maatregelen use 'urn:nl:ak:mtr:'.
    Hulpmiddelen have no id, so id_prefix=None falls back to the file stem.
    """
    if id_prefix is None:
        # Collection without frontmatter ids (hulpmiddelen): use the file stem.
        return source_file.stem

    if not isinstance(raw_id, str) or not raw_id.startswith(id_prefix):
        raise ValueError(
            f"Unexpected or missing requirement ID in {source_file.name}: {raw_id!r}"
        )

    return raw_id.removeprefix(id_prefix)


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


def strip_html_comments(text):
    """
    Remove HTML comments (<!-- ... -->) from Markdown text.

    The source files use them for author instructions ('Vul hier de
    relevante bronnen in...') and for markers that MkDocs replaces with
    generated content ('<!-- tags -->', '<!-- list_vereisten_on_... -->').
    None of that is content, and an instruction-only section would otherwise
    show up as a populated cell.
    """
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

 """Normalise an H2 Markdown heading for use as a dataframe field name."""
def normalise_heading(heading):
    # Remove MkDocs attributes such as: { data-search-exclude }
    heading = re.sub(r"\s*\{.*?\}\s*$", "", heading).strip()

    # Collapse repeated whitespace/newlines and use lowercase consistently.
    heading = re.sub(r"\s+", " ", heading)

    return heading.lower()


def append_section(sections, section_name, content):
    """
    Add content to a section without losing earlier content if the same
    heading occurs more than once.
    """
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


def extract_sections(
    body,
    merge_headings=MERGE_INTO_TOELICHTING,
    standard_sections=None,
    section_aliases=None,
):
    """Extract Markdown H2 sections and their content, keyed by normalised heading."""
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
        heading = (section_aliases or {}).get(heading, heading)

        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[start:end].strip()

        if heading in merge_headings or (
            standard_sections is not None and heading not in standard_sections
        ):
            # Preserve the informative heading as part of the explanatory text
            # so no source meaning is lost when the extra column is removed.
            merged_content = f"{original_heading}\n\n{content}".strip()
            append_section(sections, "toelichting", merged_content)
        else:
            append_section(sections, heading, content)

    return sections


def extract_links(text):
    """Extract Markdown links of the form [anchor text](target)."""
    pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    return [
        {
            "text": anchor.strip(),
            "target": target.strip(),
        }
        for anchor, target in pattern.findall(text)
    ]


def make_export_safe(value):
    """Convert a nested Python value to a JSON string for CSV/Excel export."""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)

    return value


def as_individual_values(value):
    """
    Return metadata as individual values for vocabulary analysis.

    YAML lists remain separate values. Scalar values are treated as a
    one-element list. Missing values return an empty list.
    """
    if value is None:
        return []

    # pandas may represent missing scalar values as NaN.
    if not isinstance(value, (list, dict)) and pd.isna(value):
        return []

    if isinstance(value, list):
        return value

    return [value]


def vocabulary_value_to_string(value):
    """Convert a vocabulary item to a stable printable representation."""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    return str(value)


# ---------------------------------------------------------
# STAGE 1: MINE A MARKDOWN COLLECTION
# ---------------------------------------------------------


def mine_markdown_collection(
    source_dir: Path,
    excluded_files: set,
    id_prefix: Optional[str],
    id_column: str = "requirement_id",
    merge_headings: set = MERGE_INTO_TOELICHTING,
    standard_sections: Optional[set] = None,
    section_aliases: Optional[dict] = None,
) -> tuple:
    """
    Parse every Markdown file directly in source_dir into a flat record, and
    every Markdown link into a separate relationship entry. Subfolders
    (e.g. hulpmiddelen/documenten, hulpmiddelen/images) are not descended into.

    Used for all three collections (see COLLECTIONS); id_column names the
    record's id column ('requirement_id', 'maatregel_id', 'hulpmiddel_id').

    Raises FileNotFoundError if source_dir has no usable Markdown files, and
    ValueError if two files resolve to the same id -- both are
    treated as fatal because a silently empty or silently collided dataset
    is worse than a crash at mining time.

    Returns (records, links) as plain lists of dicts -- the caller decides
    when to turn these into dataframes.
    """
    all_files = sorted(source_dir.glob("*.md"))
    files = [
        filepath
        for filepath in all_files
        if filepath.name not in excluded_files
    ]

    if not files:
        raise FileNotFoundError(
            f"No usable Markdown files found in {source_dir} "
            f"(found {len(all_files)} '.md' file(s) total, "
            f"{len(excluded_files)} name(s) excluded). "
            "Check that the path is correct and the repository is checked out there."
        )

    print(f"Found {len(all_files)} Markdown files in {source_dir}.")
    print(f"Excluded {len(all_files) - len(files)} overview file(s).")
    print(f"Processing {len(files)} files.")

    records = []
    links = []

    for filepath in files:
        text = filepath.read_text(encoding="utf-8")

        metadata, body = split_frontmatter(text)
        body = strip_html_comments(body)
        sections = extract_sections(
            body, merge_headings, standard_sections, section_aliases
        )

        # Extract requirement id from Algoritmekader URN.
        raw_id = metadata.get("id")
        requirement_id = extract_requirement_id(raw_id, filepath, id_prefix)

        # Record started with clean structure.
        record = {
            id_column: requirement_id,
            "title": metadata.get("title"),
            "source_file": filepath.name,
        }

        # Copy YAML metadata fields into the record, except for those that
        # are already explicitly handled or known to be irrelevant.
        for key, value in metadata.items():
            if key in METADATA_KEYS_HANDLED_ELSEWHERE:
                continue

            record[key] = value

        # Put Markdown H2 sections in their own columns.
        for section_name, content in sections.items():
            record[f"section_{section_name}"] = content

        # Drop known web-rendering artefacts and content that belongs in
        # separate source datasets rather than in the requirement table.
        for field in DROP_FIELDS:
            record.pop(field, None)

        records.append(record)

        # Extract every explicit Markdown link into a separate relationship table.
        links.extend(
            {
                id_column: requirement_id,
                "source_file": filepath.name,
                "anchor_text": link["text"],
                "target": link["target"],
            }
            for link in extract_links(body)
        )

    id_counts = Counter(record[id_column] for record in records)
    duplicate_ids = {
        requirement_id: count
        for requirement_id, count in id_counts.items()
        if count > 1
    }

    if duplicate_ids:
        raise ValueError(
            f"Duplicate {id_column} values found across source files -- each "
            "ID must resolve to exactly one file, or downstream vocabulary "
            f"counts and joins will silently double-count: {duplicate_ids}"
        )

    return records, links


# ---------------------------------------------------------
# STAGE 2: BUILD DATAFRAMES
# ---------------------------------------------------------


def build_dataframes(records: list, links: list) -> tuple:
    """Turn the mined records/links into dataframes and drop known artefacts."""
    df = pd.DataFrame(records)
    links_df = pd.DataFrame(links)

    # Defensive cleanup in case a dropped field exists only in some records.
    df = df.drop(columns=list(DROP_FIELDS), errors="ignore")

    return df, links_df


# ---------------------------------------------------------
# STAGE 3: ATTACH SOURCE-SNAPSHOT PROVENANCE
# ---------------------------------------------------------


def attach_provenance(df: pd.DataFrame, repo_root: Path) -> tuple:
    """
    Record which exact commit of the Algoritmekader repository this run was
    extracted from, and when the extraction itself was run.

    The repo can move on after this run, so both timestamps matter: the
    commit date says how current the *source* was, and extraction_run_at
    says when this dataset was produced from it.

    Returns (df, repo_snapshot) — the snapshot is also returned separately
    so the summary can report it without re-reading it off the dataframe.
    """
    repo_snapshot = get_repo_snapshot(repo_root)

    df["repo_commit_hash"] = repo_snapshot["commit_hash"]
    df["repo_commit_date"] = repo_snapshot["commit_date"]
    df["repo_working_tree_dirty"] = repo_snapshot["working_tree_dirty"]
    df["extraction_run_at"] = datetime.now().astimezone().isoformat(timespec="seconds")

    return df, repo_snapshot


# ---------------------------------------------------------
# STAGE 4: EXPLODE CONTROLLED VOCABULARIES
# ---------------------------------------------------------


def explode_vocabularies(
    df: pd.DataFrame,
    vocabulary_fields: list,
    id_column: str = "requirement_id",
) -> tuple:
    """
    Expand list-valued vocabulary fields into individual requirement-field-
    value assignments, then count how often each individual value occurs.

    Returns (vocabulary_assignments_df, vocabulary_counts_df).
    """
    vocabulary_assignments = []

    for _, row in df.iterrows():
        for field in vocabulary_fields:
            if field not in df.columns:
                continue

            for value in as_individual_values(row.get(field)):
                vocabulary_assignments.append(
                    {
                        id_column: row.get(id_column),
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
            .agg(count=(id_column, "nunique"))
        )

        vocabulary_counts_df["coverage_pct"] = (
            vocabulary_counts_df["count"] / len(df) * 100
        ).round(2)

        vocabulary_counts_df = vocabulary_counts_df.sort_values(
            ["field", "count", "value"],
            ascending=[True, False, True],
        ).reset_index(drop=True)

    return vocabulary_assignments_df, vocabulary_counts_df


# ---------------------------------------------------------
# STAGE 5: COLUMN PROFILE
# ---------------------------------------------------------


def count_unique_values(
    column: str,
    df: pd.DataFrame,
    vocabulary_assignments_df: pd.DataFrame,
    vocabulary_fields: list,
) -> int:
    """
    Count semantic values rather than serialized list combinations
    for controlled/multi-valued fields.
    """
    if column in vocabulary_fields:
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


def build_column_profile(
    df: pd.DataFrame,
    vocabulary_assignments_df: pd.DataFrame,
    vocabulary_fields: list,
    provenance_columns: set,
) -> pd.DataFrame:
    """Profile every requirement column, excluding provenance columns added in stage 3."""
    return pd.DataFrame(
        [
            {
                "column": column,
                "non_empty": int(df[column].notna().sum()),
                "total_rows": len(df),
                "unique_values": count_unique_values(
                    column, df, vocabulary_assignments_df, vocabulary_fields
                ),
                "coverage_pct": round(
                    df[column].notna().mean() * 100,
                    1,
                ),
            }
            for column in df.columns
            if column not in provenance_columns
        ]
    )


# ---------------------------------------------------------
# STAGE 5b: CROSS-REFERENCES BETWEEN COLLECTIONS
# ---------------------------------------------------------


def build_cross_references(collections: dict) -> pd.DataFrame:
    """
    Turn the list-valued 'vereiste' / 'maatregel' frontmatter fields into one
    edge table: which record points at which record in another collection.

    collections maps collection name -> (df, id_column). Frontmatter targets
    are file name stems, so each target is resolved to the compact id of the
    target collection via source_file. Unresolvable targets are kept with
    target_id=None and resolved=False rather than dropped, so broken links in
    the source repository stay visible.
    """
    stem_to_id = {
        name: {
            Path(row["source_file"]).stem: row[id_column]
            for _, row in df.iterrows()
        }
        for name, (df, id_column) in collections.items()
    }

    edges = []

    for name, (df, id_column) in collections.items():
        for _, row in df.iterrows():
            for field, target_collection in CROSS_REFERENCE_FIELDS.items():
                if field not in df.columns:
                    continue

                for target in as_individual_values(row.get(field)):
                    target_id = stem_to_id.get(target_collection, {}).get(target)
                    edges.append(
                        {
                            "source_collection": name,
                            "source_id": row[id_column],
                            "source_file": row["source_file"],
                            "relation_field": field,
                            "target_collection": target_collection,
                            "target_stem": target,
                            "target_id": target_id,
                            "resolved": target_id is not None,
                        }
                    )

    columns = [
        "source_collection", "source_id", "source_file", "relation_field",
        "target_collection", "target_stem", "target_id", "resolved",
    ]
    edges_df = pd.DataFrame(edges, columns=columns)

    unresolved = int((~edges_df["resolved"]).sum()) if not edges_df.empty else 0
    if unresolved:
        # Only meaningful when the target collection was actually mined.
        print(f"WARNING: {unresolved} cross-reference(s) could not be resolved.")

    return edges_df


# ---------------------------------------------------------
# STAGE 5c: BEGRIPPENLIJST (GLOSSARY)
# ---------------------------------------------------------

# Abbreviation-style definition line used by MkDocs:  *[term]: definition
GLOSSARY_LINE = re.compile(r"^\*\[(?P<term>[^\]]+)\]:\s*(?P<definition>.*)$")


def mine_begrippenlijst(filepath: Path) -> pd.DataFrame:
    """
    Parse docs/begrippenlijst.md into one row per defined term.

    The file is not frontmatter + sections but a flat list of '*[term]: text'
    lines. Terms that occur more than once (the source defines e.g.
    'geharmoniseerde norm' twice with different wording) are all kept and
    flagged with duplicate_term, rather than silently picking one.
    Non-blank lines that do not match the pattern are reported, not dropped
    silently.
    """
    if not filepath.is_file():
        raise FileNotFoundError(f"Begrippenlijst not found: {filepath}")

    rows = []

    for line_number, line in enumerate(
        filepath.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue

        match = GLOSSARY_LINE.match(line)

        if not match:
            print(f"WARNING: {filepath.name}:{line_number} is not a term definition: {line[:60]!r}")
            continue

        rows.append(
            {
                "term": match.group("term").strip(),
                "definition": re.sub(r"\s+", " ", match.group("definition")).strip(),
                "source_file": filepath.name,
                "source_line": line_number,
            }
        )

    df = pd.DataFrame(rows, columns=["term", "definition", "source_file", "source_line"])

    df["term_normalised"] = df["term"].str.lower()
    df["duplicate_term"] = df["term_normalised"].duplicated(keep=False)

    print(f"Found {len(df)} glossary entries in {filepath.name} "
          f"({int(df['duplicate_term'].sum())} share a term with another entry).")

    return df


# ---------------------------------------------------------
# STAGE 6: EXPORT
# ---------------------------------------------------------


def export_outputs(
    df: pd.DataFrame,
    links_df: pd.DataFrame,
    vocabulary_assignments_df: pd.DataFrame,
    vocabulary_counts_df: pd.DataFrame,
    column_profile_df: pd.DataFrame,
    output_dir: Path,
    name: str = "vereisten",
) -> dict:
    """
    Write one collection's Excel workbook and CSV exports. Returns their paths.

    Files are prefixed with the collection name, so the vereisten outputs keep
    their V2 file names ('vereisten.xlsx', 'vereisten.csv', ...).
    """
    output_dir.mkdir(exist_ok=True)

    # Keep Python lists intact until all analysis/counting is complete. Only
    # now serialize them for flat-file output.
    export_df = df.map(make_export_safe)

    excel_path = output_dir / f"{name}.xlsx"

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        export_df.to_excel(writer, sheet_name=name, index=False)
        links_df.to_excel(writer, sheet_name="links", index=False)
        vocabulary_assignments_df.to_excel(
            writer, sheet_name="vocabulary_assignments", index=False
        )
        vocabulary_counts_df.to_excel(
            writer, sheet_name="vocabulary_counts", index=False
        )
        column_profile_df.to_excel(
            writer, sheet_name="column_profile", index=False
        )

    csv_path = output_dir / f"{name}.csv"
    vocabulary_counts_path = output_dir / f"{name}_vocabulary_counts.csv"
    vocabulary_assignments_path = output_dir / f"{name}_vocabulary_assignments.csv"

    export_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    vocabulary_counts_df.to_csv(
        vocabulary_counts_path, index=False, encoding="utf-8-sig"
    )
    vocabulary_assignments_df.to_csv(
        vocabulary_assignments_path, index=False, encoding="utf-8-sig"
    )

    return {
        "excel_path": excel_path,
        "csv_path": csv_path,
        "vocabulary_counts_path": vocabulary_counts_path,
        "vocabulary_assignments_path": vocabulary_assignments_path,
    }


def export_cross_references(edges_df: pd.DataFrame, output_dir: Path) -> Path:
    """Write the vereiste/maatregel cross-reference edge table."""
    output_dir.mkdir(exist_ok=True)
    path = output_dir / "cross_references.csv"
    edges_df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def export_begrippenlijst(df: pd.DataFrame, output_dir: Path) -> dict:
    """Write the glossary as Excel and CSV."""
    output_dir.mkdir(exist_ok=True)
    excel_path = output_dir / "begrippenlijst.xlsx"
    csv_path = output_dir / "begrippenlijst.csv"

    df.to_excel(excel_path, sheet_name="begrippenlijst", index=False)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    return {"excel_path": excel_path, "csv_path": csv_path}


# ---------------------------------------------------------
# STAGE 7: SUMMARY
# ---------------------------------------------------------


def print_summary(
    df: pd.DataFrame,
    repo_snapshot: dict,
    vocabulary_counts_df: pd.DataFrame,
    vocabulary_fields: list,
    output_paths: dict,
    label: str = "Requirements",
) -> None:
    print()
    print(f"Done: {label}.")
    print(f"Source commit: {repo_snapshot['commit_hash']} ({repo_snapshot['commit_date']})")
    print(f"Working tree dirty: {repo_snapshot['working_tree_dirty']}")
    print(f"Extraction run at: {df['extraction_run_at'].iloc[0]}")
    print(f"{label}: {len(df)}")
    print(f"Excel: {output_paths['excel_path']}")
    print(f"CSV: {output_paths['csv_path']}")
    print(f"Vocabulary counts: {output_paths['vocabulary_counts_path']}")
    print(f"Vocabulary assignments: {output_paths['vocabulary_assignments_path']}")

    print()
    print("Columns retained:")
    for column in df.columns:
        print(f" - {column}")

    print()
    print("Vocabulary values found:")
    for field in vocabulary_fields:
        if vocabulary_counts_df.empty:
            break

        subset = vocabulary_counts_df[vocabulary_counts_df["field"] == field]

        if subset.empty:
            continue

        print(f"\n{field} ({len(subset)} individual values)")
        for _, vocab_row in subset.iterrows():
            print(
                f" - {vocab_row['value']}: "
                f"{int(vocab_row['count'])} records "
                f"({vocab_row['coverage_pct']}%)"
            )


# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------

ALL_SOURCES = [*COLLECTIONS, "begrippenlijst"]


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Mine the Algoritmekader vereisten, maatregelen, hulpmiddelen and "
            "begrippenlijst into structured CSV/Excel exports (V3 "
            "data-preparation stage)."
        )
    )
    parser.add_argument(
        "--docs-dir",
        type=Path,
        default=DOCS_DIR,
        help=f"The Algoritmekader 'docs' directory (default: {DOCS_DIR})",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ALGORITMEKADER_REPO_ROOT,
        help=f"Root of the Algoritmekader git checkout, for provenance (default: {ALGORITMEKADER_REPO_ROOT})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help=f"Directory to write CSV/Excel exports to (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=ALL_SOURCES,
        default=ALL_SOURCES,
        help="Mine only these sources (default: all)",
    )
    return parser.parse_args()


# ---------------------------------------------------------
# PIPELINE
# ---------------------------------------------------------


def run_collection(name: str, config: dict, docs_dir: Path, repo_root: Path, output_dir: Path):
    """Run stages 1-7 for one collection. Returns (df, id_column)."""
    id_column = config["id_column"]
    vocabulary_fields = config["vocabulary_fields"]
    source_dir = docs_dir / "voldoen-aan-wetten-en-regels" / config["subdir"]

    print(f"\n=== {name} ===")

    records, links = mine_markdown_collection(
        source_dir,
        EXCLUDED_FILES,
        config["id_prefix"],
        id_column=id_column,
        merge_headings=config["merge_headings"],
        standard_sections=config.get("standard_sections"),
        section_aliases=config.get("section_aliases"),
    )

    df, links_df = build_dataframes(records, links)

    df, repo_snapshot = attach_provenance(df, repo_root)

    vocabulary_assignments_df, vocabulary_counts_df = explode_vocabularies(
        df, vocabulary_fields, id_column
    )

    column_profile_df = build_column_profile(
        df, vocabulary_assignments_df, vocabulary_fields, PROVENANCE_COLUMNS
    )

    output_paths = export_outputs(
        df,
        links_df,
        vocabulary_assignments_df,
        vocabulary_counts_df,
        column_profile_df,
        output_dir,
        name=name,
    )

    print_summary(
        df, repo_snapshot, vocabulary_counts_df, vocabulary_fields,
        output_paths, label=name,
    )

    return df, id_column


def main():
    args = parse_args()

    mined = {}

    for name, config in COLLECTIONS.items():
        if name in args.only:
            mined[name] = run_collection(
                name, config, args.docs_dir, args.repo_root, args.output_dir
            )

    if len(mined) > 1:
        edges_df = build_cross_references(mined)
        path = export_cross_references(edges_df, args.output_dir)
        print(f"\nCross-references: {len(edges_df)} edges -> {path}")

    if "begrippenlijst" in args.only:
        print("\n=== begrippenlijst ===")
        glossary_df = mine_begrippenlijst(args.docs_dir / "begrippenlijst.md")
        glossary_df, _ = attach_provenance(glossary_df, args.repo_root)
        paths = export_begrippenlijst(glossary_df, args.output_dir)
        print(f"Excel: {paths['excel_path']}\nCSV: {paths['csv_path']}")


if __name__ == "__main__":
    main()
