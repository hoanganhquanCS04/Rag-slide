import chromadb
c = chromadb.PersistentClient(path="/home/agentic-dgx-01/workspace/Rag-slide/src/output/vectordb/chroma")   # đúng path trong code
for col in c.list_collections():
    print(col.name, c.get_collection(col.name).count())