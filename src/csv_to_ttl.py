import csv
import re
from pathlib import Path
from rdflib import Graph, Literal, Namespace
from rdflib import RDF, RDFS, URIRef
from rdflib.namespace import OWL

# Define the path to the ontology directory
ONTOLOGY_DIR = Path("/Users/johannadekker/thesis-ontology")
ONTOLOGY_IRI = "http://www.semanticweb.org/johannadekker/ontologies/2026/9/NL-AI-Governance"
NL = Namespace(ONTOLOGY_IRI + "/")
INSTANCES_IRI = URIRef(ONTOLOGY_IRI + "-instances")

file_names = ["vereisten", "maatregelen", "hulpmiddelen"]
id_columns = ["requirement_id", "maatregel_id", "hulpmiddel_id"]
prefixes   = ["requirement", "measure", "tool"]
class_names = ["Requirement", "Measure", "Tool"]

# Mapping of CSV fields to ontology classes and predicates
# Edit this to expand automatic population of the ontology with more fields from the CSV files
fields = {
    "categorie": (NL.Tool, RDF.type),
    "levenscyclus": (NL.LifecycleStage, NL.appliesToStage),
    "rollen": (NL.AlgoritmekaderRole, NL.relevantForRole),
    "rol-ai-act": (NL.AIActRole, NL.appliesToRole),
}

relations = {
    ("maatregelen", "vereiste"): NL.implements,
    ("hulpmiddelen", "vereiste"): NL.supportsRequirement,
    ("hulpmiddelen", "maatregel"): NL.supportsMeasure,
}


# make the text in the ontology and csvs easy to match by normalising it
# removes IRI namespaces, lowercases, removes "stage-" and "role-" prefixes, and removes hyphens, underscores, and whitespace
def normalise(text):
    text = str(text).removeprefix(str(NL)).lower().removeprefix("stage-").removeprefix("role-")
    return re.sub(r"[-_\s]", "", text)


# Create an instance of the class for every row of the csv, with its Dutch title as label
def add_instances(g, filename, class_name, id_column, prefix):
    with open(f"output/{filename}.csv", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            node = NL[f"{prefix}-{row[id_column]}"]
            g.add((node, RDF.type, NL[class_name]))
            g.add((node, RDFS.label, Literal(row["title"], lang="nl")))


# Build a lookup table for the subclasses or instances directly under a given parent class in the ontology,
# mapping normalised names and labels to their corresponding URIs.
def build_lookup(base, parent):
    children = set(base.subjects(RDFS.subClassOf, parent)) | set(base.subjects(RDF.type, parent))
    lookup = {}
    for child in children:
        for name in [child, *base.objects(child, RDFS.label)]:
            key = normalise(name)
            if lookup.get(key, child) != child:
                raise SystemExit(f"Ambiguous match for {key}: {lookup[key]} vs {child}")
            lookup[key] = child
    return lookup


# Add one triple per row of the assignments table for the given field.
def populate(g, lookup, csv_path, id_column, prefix, field, predicate):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = [row for row in csv.DictReader(f) if row["field"] == field]

    missing = {row["value"] for row in rows if normalise(row["value"]) not in lookup}
    if missing:
        raise SystemExit(f"Missing {field} values in ontology: {sorted(missing)}")

    for row in rows:
        g.add((NL[f"{prefix}-{row[id_column]}"], predicate, lookup[normalise(row["value"])]))
    print(f"{field}: {len(rows)} assignments added")


# Create all instances from the csv files
def add_all_instances(g):
    for file, id_column, prefix, class_name in zip(file_names, id_columns, prefixes, class_names):
        add_instances(g, file, class_name, id_column, prefix)


# Add all field assignments (categories, lifecycle stages, roles) from the csv files
def add_all_assignments(g, base):
    for field, (parent, predicate) in fields.items():
        lookup = build_lookup(base, parent)
        for file, id_column, prefix in zip(file_names, id_columns, prefixes):
            populate(g, lookup, f"output/{file}_vocabulary_assignments.csv", id_column, prefix, field, predicate)

# Add the links between items (measure implements requirement, tool supports measure, ...)
def add_relations(g):
    prefix_of = dict(zip(file_names, prefixes))
    added = len(g)
    with open("output/cross_references.csv", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = (row["source_collection"], row["relation_field"])
            if key in relations and row["resolved"] == "True":
                source = NL[f"{prefix_of[row['source_collection']]}-{row['source_id']}"]
                target = NL[f"{prefix_of[row['target_collection']]}-{row['target_id']}"]
                g.add((source, relations[key], target))
    print(f"relations: {len(g) - added} links added")
    
def add_instances_header(g):
    """Declare the instances ontology and its import of the main ontology."""
    g.add((INSTANCES_IRI, RDF.type, OWL.Ontology))
    g.add((INSTANCES_IRI, OWL.imports, URIRef(ONTOLOGY_IRI)))


def merge(base, instances):
    """Combine ontology and instances into one ontology, without the instances header."""
    merged = base + instances
    merged.remove((INSTANCES_IRI, None, None))
    return merged


def save(graph, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(path, format="turtle")


def main():
    base = Graph().parse(ONTOLOGY_DIR / "thesis-ontology.ttl", format="turtle")
    g = Graph()
    g.bind("", NL)

    add_all_instances(g)
    add_all_assignments(g, base)
    add_relations(g)
    add_instances_header(g)

    save(g, ONTOLOGY_DIR / "wip-ontology.ttl")
    save(merge(base, g), ONTOLOGY_DIR / "releases" / "thesis-ontology-v0.1.ttl")


if __name__ == "__main__":
    main()