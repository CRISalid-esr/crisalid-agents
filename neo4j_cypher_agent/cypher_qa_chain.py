import json
import os
from pathlib import Path

from langchain_core.prompts import PromptTemplate
from langchain_neo4j import GraphCypherQAChain, Neo4jGraph

from neo4j_cypher_agent.llm import build_chat_model

BASE_DIR = Path(__file__).resolve().parent

FEWSHOT_PATH = BASE_DIR / "fewshot_examples.json"
CYPHER_PROMPT_PATH = BASE_DIR / "cypher_generation_prompt.txt"


def load_fewshot_examples() -> str:
    with FEWSHOT_PATH.open("r", encoding="utf-8") as f:
        examples = json.load(f)

    return "\n\n".join(f"- {example}" for example in examples)


def load_prompt_template() -> str:
    return CYPHER_PROMPT_PATH.read_text(encoding="utf-8")


def build_cypher_generation_prompt() -> PromptTemplate:
    return PromptTemplate(
        input_variables=["schema", "question", "fewshot_examples"],
        template=load_prompt_template(),
    )


def cypher_qa_chain() -> GraphCypherQAChain:
    graph = Neo4jGraph(
        url=os.environ["NEO4J_URI"],
        username=os.environ["NEO4J_USERNAME"],
        password=os.environ["NEO4J_PASSWORD"],
    )

    graph.refresh_schema()

    llm = build_chat_model()
    fewshot_examples = load_fewshot_examples()
    cypher_generation_prompt = build_cypher_generation_prompt()

    return GraphCypherQAChain.from_llm(
        llm=llm,
        graph=graph,
        cypher_prompt=cypher_generation_prompt.partial(
            fewshot_examples=fewshot_examples,
        ),
        validate_cypher=True,
        verbose=True,
        allow_dangerous_requests=True,
    )
