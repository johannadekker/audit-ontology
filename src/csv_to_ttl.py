import csv
import re
from rdflib import Graph, Literal, Namespace
from rdflib import RDF, RDFS, URIRef
from rdflib.namespace import OWL

file_names = ["vereisten", "maatregelen", "hulpmiddelen"]
id_columns = ["requirement_id", "maatregel_id", "hulpmiddel_id"]
prefixes   = ["requirement", "measure", "tools"]
class_names = ["Requirement", "Measure", "Tools"]

#open the CSV file and read its contents into a list of dictionaries
def open_file(filename):
    with open("output/" + filename + ".csv", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return rows

NL = Namespace("http://www.semanticweb.org/johannadekker/ontologies/2026/9/NL-AI-Governance/")
g = Graph()
g.bind("", NL)

# create instances of the classes and add them to the graph
def get_instance(class_name, id_column, prefix, rows):
    for row in rows:
        node = NL[prefix + "-" + row[id_column]]
        g.add((node, RDF.type, NL[class_name]))
        g.add((node, RDFS.label, Literal(row["title"], lang="nl")))
    return rows

# loop through the file names and create instances of the classes
for i in range(len(file_names)):
    rows = open_file(file_names[i])
    get_instance(class_names[i], id_columns[i], prefixes[i], rows)

def normalise(text):
    """'Impact-assessment' -> 'impactassessment'"""
    return re.sub(r"[-_\s]", "", str(text).lower())


def build_lookup(base, parent, mode):
    """Map normalised names to the classes (or individuals) under `parent`."""
    if mode == "subclass":
        candidates = set(base.transitive_subjects(RDFS.subClassOf, parent)) - {parent}
    elif mode == "instance":
        candidates = set(base.subjects(RDF.type, parent))
    else:
        raise ValueError(f"mode must be 'subclass' or 'instance', got {mode!r}")

    lookup = {}
    for item in candidates:
        names = {normalise(str(item).removeprefix(str(NL)))}
        names |= {normalise(label) for label in base.objects(item, RDFS.label)}
        for name in names:
            if name in lookup and lookup[name] != item:
                raise SystemExit(f"Ambiguous match for '{name}': {lookup[name]} and {item}")
            lookup[name] = item
    return lookup


def populate(g, lookup, assignments_path, id_column, id_prefix, field, mode,
             predicate=None, skip_values=()):
    """Add one triple per row of the assignments table for the given field."""
    if mode == "instance" and predicate is None:
        raise ValueError("instance mode needs a predicate")
    unknown, count = set(), 0
    with open(assignments_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row["field"] != field or row["value"] in skip_values:
                continue
            target = lookup.get(normalise(row["value"]))
            if target is None:
                unknown.add(row["value"])
                continue
            node = NL[f"{id_prefix}-{row[id_column]}"]
            g.add((node, RDF.type, target) if mode == "subclass"
                  else (node, predicate, target))
            count += 1
    if unknown:
        raise SystemExit(f"Field '{field}': no match in the ontology for {sorted(unknown)}")
    print(f"{field}: {count} assignments added")

base = Graph()
base.parse("/Users/johannadekker/thesis-ontology/thesis-ontology.ttl", format="turtle")

populate(g, build_lookup(base, NL.Tools, "subclass"),
         "output/hulpmiddelen_vocabulary_assignments.csv",
         "hulpmiddel_id", "tools", "categorie", "subclass")

## populate(g, build_lookup(base, NL.RiskGroup, "instance"),
        ##sed "output/vereisten_vocabulary_assignments.csv",
        ## "requirement_id", "requirement", "risicogroep", "instance",
        ## predicate=NL.appliesToRiskGroup,
        ## skip_values=("niet-van-toepassing", "uitzondering-van-toepassing"))

inst = URIRef("http://www.semanticweb.org/johannadekker/ontologies/2026/9/NL-AI-Governance-instances")
g.add((inst, RDF.type, OWL.Ontology))
g.add((inst, OWL.imports, URIRef("http://www.semanticweb.org/johannadekker/ontologies/2026/9/NL-AI-Governance")))

g.serialize("/Users/johannadekker/thesis-ontology/wip-ontology.ttl", format="turtle")
merged = Graph()
merged.parse("/Users/johannadekker/thesis-ontology/thesis-ontology.ttl", format="turtle")
merged.parse("/Users/johannadekker/thesis-ontology/wip-ontology.ttl", format="turtle")
merged.remove((None, OWL.imports, None))   # the import is no longer needed
merged.serialize("/Users/johannadekker/thesis-ontology/releases/thesis-ontology-v0.1.ttl", format="turtle")