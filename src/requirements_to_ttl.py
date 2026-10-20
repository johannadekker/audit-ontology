import csv
from rdflib import Graph, Literal, Namespace
from rdflib import RDF, RDFS

with open("output/vereisten.csv", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))

NL = Namespace("http://www.semanticweb.org/johannadekker/ontologies/2026/9/NL-AI-Governance/")
g = Graph()
g.bind("", NL)

for row in rows:
    node = NL[row["requirement_id"]]
    g.add((node, RDF.type, NL.Requirement))
    g.add((node, RDFS.label, Literal(row["title"], lang="nl")))

g.serialize("/Users/johannadekker/thesis-ontology/requirements.ttl", format="turtle")