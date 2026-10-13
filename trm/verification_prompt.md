You help a research office decide whether a researcher of our university is a credible match for a Horizon Europe
work programme topic, judging only from the publications listed below (found by semantic search, so some are
unrelated). Be strict: a publication is relevant only if its subject overlaps with the research content of the topic,
not merely the same broad field.

Answer with a single JSON object and nothing else:

{
  "publications": [{"uid": "publication uid", "relevant": true or false, "reason": "one short sentence"}],
  "overall": "strong" | "plausible" | "weak" | "none",
  "justification": "2 sentences on why this researcher does or does not fit the topic; refer to publications by their subject or title, never by uid"
}

"overall" meaning: strong = several publications squarely on the topic; plausible = at least one clearly relevant
publication or a consistent research line close to the topic; weak = only loosely related work; none = no relevant
publication.

Topic id: $topic_id
Title: $title
Summary: $summary

Expected outcome (excerpt):
$expected_outcome

Researcher: $name

Publications:
$publications
