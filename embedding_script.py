import os
from tqdm import tqdm # Isse progress bar dikhayega
from langchain_community.document_loaders import DirectoryLoader, BSHTMLLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS

WIKI_PATH = "/usr/share/doc/arch-wiki/html/en"

print("Loading files...")
loader = DirectoryLoader(WIKI_PATH, glob="**/*.html", loader_cls=BSHTMLLoader)
docs = loader.load()

text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
chunks = text_splitter.split_documents(docs)
print(f"Total chunks: {len(chunks)}")

# Batching add ki hai yahan
print("Generating embeddings...")
embedding_model = HuggingFaceEmbeddings(
    model_name="all-MiniLM-L6-v2",
    encode_kwargs={'batch_size': 64} # Yahan batch size increase kar diya
)

# Chunks ko batch mein process karo
vector_store = None
for i in tqdm(range(0, len(chunks), 100)): # 100 chunks ka batch
    batch = chunks[i:i+100]
    if vector_store is None:
        vector_store = FAISS.from_documents(batch, embedding_model)
    else:
        vector_store.add_documents(batch)

vector_store.save_local("arch_wiki_index")
print("Done!")
