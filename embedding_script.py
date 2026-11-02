"""Build an optional documentation index from operator-supplied local HTML files."""

import argparse
import os
from pathlib import Path

from qareen_core.config import data_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, default=data_dir() / "arch_wiki_index")
    args = parser.parse_args()
    from bs4 import BeautifulSoup
    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    from qareen_embeddings import RemoteEmbeddings

    documents = []
    for file in sorted(args.directory.rglob("*.html")):
        soup = BeautifulSoup(file.read_text(errors="replace"), "html.parser")
        for tag in soup(["script", "style", "nav"]):
            tag.decompose()
        documents.append(
            Document(page_content=soup.get_text(" ", strip=True), metadata={"source": str(file)})
        )
    if not documents:
        parser.error("No HTML files found")
    splitter = RecursiveCharacterTextSplitter(chunk_size=700, chunk_overlap=100)
    os.umask(0o077)
    index = FAISS.from_documents(splitter.split_documents(documents), RemoteEmbeddings())
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    index.save_local(str(args.output))
    print(f"Indexed {len(documents)} files into {args.output}")


if __name__ == "__main__":
    main()
