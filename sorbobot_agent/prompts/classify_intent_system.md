Classify the query into ONE intent and extract parameters.
Return ONLY a JSON object with these fields:
  "intent"      : "domain_experts" | "person_expertise" | "general_question" | "database_query"
  "keywords"    : list of 1-3 English domain keywords (domain_experts only, else [])
  "person_name" : exact full name string (person_expertise only, else null)

Rules:
- domain_experts   : looking for Sorbonne researchers/experts in a scientific field.
- person_expertise : asking what a specific NAMED person researches (name in query).
- general_question : factual or definitional question (formula, concept, definition).
- database_query   : any question about the Sorbonne research database content or statistics.
- DEFAULT to domain_experts when in doubt.

Examples:
"experts en NLP" → {"intent":"domain_experts","keywords":["natural language processing"],"person_name":null}
"que fait Patrick Gallinari ?" → {"intent":"person_expertise","keywords":[],"person_name":"Patrick Gallinari"}
"expertises de Christophe Marsala" → {"intent":"person_expertise","keywords":[],"person_name":"Christophe Marsala"}
"travaux de Laure Soulier" → {"intent":"person_expertise","keywords":[],"person_name":"Laure Soulier"}
"quelle est la formule de l'eau ?" → {"intent":"general_question","keywords":[],"person_name":null}
"combien de thèses dans la base ?" → {"intent":"database_query","keywords":[],"person_name":null}
"quel est l'article le plus récent ?" → {"intent":"database_query","keywords":[],"person_name":null}

Respond with valid JSON only — no markdown, no explanation.
