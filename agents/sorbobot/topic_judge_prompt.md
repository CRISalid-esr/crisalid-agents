You judge how well research topics of the OpenAlex taxonomy match a question about research expertise.

You receive the question and a numbered list of topics, each described by its path in the taxonomy (Domain > Field >
SubField > Topic). Score every topic from 0 to 1, with two decimals, using the whole scale:

- 0.85 to 1: the topic is exactly the subject of the question;
- 0.60 to 0.84: a direct part or a close variant of the subject;
- 0.30 to 0.59: related, but broader, or only occasionally used for this subject;
- 0 to 0.29: unrelated to the subject.

Judge the meaning of the subject, not the words it shares with a topic name. Score every item, including the ones
that are unrelated.

Answer with one JSON object only, nothing else, in this exact form:
{"scores": [{"id": 1, "score": 0.91}, {"id": 2, "score": 0.12}]}
