from pathlib import Path
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]

FILE = PROJECT_ROOT / "output" / "vereisten.csv"

if not FILE.exists():
    raise FileNotFoundError(f"Could not find: {FILE}")

df = pd.read_csv(FILE)

print(f"Rows: {len(df)}")
print(f"Columns: {len(df.columns)}")


# Show all columns

print("\nALL COLUMNS")
print("=" * 80)

for column in df.columns:
    print(column)


# Profile columns: count non-empty and unique values

print("\nCOLUMN PROFILE")
print("=" * 80)

for column in df.columns:

    non_empty = df[column].notna().sum()
    unique = df[column].nunique(dropna=True)

    print(
        f"{column:90} "
        f"filled={non_empty:3}/{len(df)} "
        f"unique={unique:3}"
    )


# Separate metadata columns from markdown section columns based on naming convention. 
# Metadata columns do not start with "section_", while markdown section columns do. 
# This distinction allows for easier analysis and inspection of the different types of data contained in the DataFrame.

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

print("\nMETADATA COLUMNS")
print("=" * 80)

for column in metadata_columns:
    print(column)

print("\nMARKDOWN SECTION COLUMNS")
print("=" * 80)

for column in section_columns:
    print(column)


# ---------------------------------------------------------
# 4. INSPECT VALUES OF METADATA FIELDS
# ---------------------------------------------------------

print("\nMETADATA VALUES")
print("=" * 80)

for column in metadata_columns:

    print(f"\n--- {column} ---")

    values = df[column].dropna()

    if values.empty:
        print("(empty)")
        continue

    # For fields with few different values, show them all.
    if values.nunique() <= 30:
        print(values.value_counts().to_string())

    # For text-heavy fields, just show examples.
    else:
        for value in values.head(5):
            print(repr(str(value)[:300]))

# ---------------------------------------------------------
# 5. CLASSIFY COLUMNS HEURISTICALLY
# ---------------------------------------------------------

print("\nFIELD CLASSIFICATION")
print("=" * 80)

classification_rows = []

for column in df.columns:
    non_empty = df[column].notna().sum()
    unique = df[column].nunique(dropna=True)

    if column.startswith("section_"):
        category = "Markdown section"

    elif column in ["filename", "slug", "id", "title"]:
        category = "Identity / provenance"

    elif unique <= 2:
        category = "Boolean / binary metadata"

    elif unique <= 30:
        category = "Controlled / categorical metadata"

    else:
        category = "Free text / identifier"

    classification_rows.append({
        "Field": column,
        "Category": category,
        "Non-empty": non_empty,
        "Unique values": unique,
    })

classification_df = pd.DataFrame(classification_rows)

print(classification_df.to_string(index=False))
