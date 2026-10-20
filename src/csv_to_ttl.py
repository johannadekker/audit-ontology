import csv
from rdflib import Graph, Literal, Namespace
from rdflib import RDF, RDFS

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
 

g.serialize("/Users/johannadekker/thesis-ontology/requirements.ttl", format="turtle")