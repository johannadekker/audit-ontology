from pathlib import Path
from collections import Counter

from rdflib import Graph
from rdflib.namespace import RDF, RDFS, SKOS, OWL

# Path is defined relative to the location of this script, ensuring that it works regardless of the current working directory when the script is executed.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Importing relevant namespaces for RDF, RDFS, SKOS, and OWL to facilitate querying and working with the RDF graph.
AIACT_FILE = (
    PROJECT_ROOT
    / "data"
    / "DPV"
    / "2.3"
    / "legal"
    / "eu"
    / "aiact"
    / "eu-aiact.ttl"
)

print("Loading:")
print(AIACT_FILE)

# Load the RDF graph from the Turtle file
graph = Graph()
graph.parse(AIACT_FILE, format="turtle")

# Print the number of triples loaded into the RDF graph to provide feedback on the size of the dataset being worked with. This is useful for debugging and ensuring that the data has been loaded correctly.
print()
print(f"Triples loaded: {len(graph)}")

#Inspect and print the predicates + counts for insight into structure and relationships in the RDF graph. 
# This helps understand the ontology's schema and the types of relationships present between entities.
print()
print("Predicates used in the AI Act ontology:")
print()

predicate_counts = Counter(graph.predicates())

for predicate, count in predicate_counts.most_common():
    print(count, predicate)

#Inspect and print some of the concepts/classes in the RDF graph to provide a quick overview of the types of entities defined in the ontology. This can help identify key concepts and their labels, which is useful for understanding the domain being modeled.
print()
print("Some concepts/classes:")

count = 0
# Iterate over all unique subjects in the RDF graph to find and print their labels. The script first attempts to retrieve the preferred label (prefLabel) from the SKOS namespace; if that is not available, it falls back to the RDFS label. It prints the subject URI and its corresponding label, limiting the output to 20 entries for brevity.
for subject in set(graph.subjects()):

    labels = list(graph.objects(subject, SKOS.prefLabel))

    if not labels:
        labels = list(graph.objects(subject, RDFS.label))

    if labels:
        print(subject)
        print("   Label:", labels[0])
        print()

        count += 1

    if count >= 20:
        break

