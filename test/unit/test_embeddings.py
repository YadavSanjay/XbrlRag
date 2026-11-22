
import numpy as np

from src.retriever.base_retriever import BaseRetriever

if __name__ == "__main__":
    # Set numpy print options to show full arrays
    np.set_printoptions(threshold=np.inf, linewidth=np.inf)

    retriever = BaseRetriever()
    query = "For fiscal year 2021 what were Alphabet's repurchases of Class A and Class C common stock (share repurchases) — stated as total dollars"
    query_results  = retriever.retrieve_chunks_with_text(query)

    for id, (chunk_text, is_table, table_path) in query_results.items():
        print(f" chunk_text: \n{chunk_text}\n")
        print(f" is_table:{is_table} | table_json:{table_path} \n\n")

