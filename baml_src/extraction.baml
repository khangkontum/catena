enum CellStatus {
  ANSWERED @description("The provided evidence directly supports an answer.")
  NOT_REPORTED @description("The provided evidence does not report the requested information.")
  UNCERTAIN @description("The evidence is related but insufficient or ambiguous.")
}

enum Confidence {
  HIGH
  MEDIUM
  LOW
}

class EvidenceQuote {
  quote string @description("A short verbatim quote from the evidence context.")
  page int? @description("Page number if present in the context metadata.")
  chunk_id string? @description("Chunk id from the evidence context metadata.")
}

class CellExtraction {
  status CellStatus
  answer string @description("A concise atomic answer. Use exactly 'Not reported.' when status is NOT_REPORTED.")
  value string? @description("A normalized scalar value when possible, for example '128', '6 months', or 'randomized controlled trial'.")
  evidence EvidenceQuote[] @description("Direct quotes supporting the answer. Empty if not reported.")
  confidence Confidence
  rationale string? @description("Brief explanation of how the evidence supports the answer or why it is missing.")
}

class AnswerEvidenceQuote {
  paper_id int @description("Global paper id from the evidence context metadata.")
  paper_title string? @description("Paper title from the evidence context metadata.")
  quote string @description("A short verbatim quote from the evidence context.")
  page int? @description("Page number if present in the context metadata.")
  chunk_id string? @description("Chunk id from the evidence context metadata.")
}

class PaperQuestionAnswer {
  answer string @description("Concise answer to the user's one-off question. Use exactly 'Not enough evidence.' if unsupported by the context.")
  evidence AnswerEvidenceQuote[] @description("Direct quotes supporting the answer. Empty if unsupported.")
  confidence Confidence
  rationale string? @description("Brief explanation of how the evidence supports the answer or why it is missing.")
}

function ExtractCell(
  paper_title: string,
  column_name: string,
  column_prompt: string,
  evidence_context: string
) -> CellExtraction {
  client Gateway
  prompt #"
    You extract one atomic table cell from one research paper.

    Paper title:
    {{ paper_title }}

    Column name:
    {{ column_name }}

    Extraction question/instruction:
    {{ column_prompt }}

    Evidence context from this paper only:
    ---
    {{ evidence_context }}
    ---

    Rules:
    - Answer only from the evidence context above.
    - Do not use outside knowledge.
    - Keep the answer atomic and concise.
    - Prefer a direct normalized value when the question asks for a number, duration, category, or short phrase.
    - Include short verbatim quotes that support the answer.
    - If the evidence context does not contain the requested information, set status to NOT_REPORTED, answer to exactly "Not reported.", confidence to HIGH, and evidence to [].
    - If the context is relevant but ambiguous, set status to UNCERTAIN and explain briefly in rationale.

    {{ ctx.output_format }}
  "#
}

function AnswerQuestion(
  question: string,
  evidence_context: string
) -> PaperQuestionAnswer {
  client Gateway
  prompt #"
    You answer one standalone question over one or more research papers.

    User question:
    {{ question }}

    Evidence context:
    ---
    {{ evidence_context }}
    ---

    Rules:
    - This is a one-off question. There is no chat history.
    - Use only the evidence context in this exact message.
    - Do not use outside knowledge.
    - Synthesize across papers only when the question asks for comparison or multiple-paper evidence.
    - Include direct quotes for every substantive claim.
    - Use at most 4 evidence quotes total.
    - Each evidence quote must carry the paper_id, paper_title if available, page if available, and chunk_id from the context metadata.
    - If the evidence context does not support an answer, set answer to exactly "Not enough evidence.", confidence to HIGH, and evidence to [].

    {{ ctx.output_format }}
  "#
}

function AnswerQuestionFromContext(
  question: string,
  context_label: string,
  evidence_context: string
) -> PaperQuestionAnswer {
  client Gateway
  prompt #"
    You answer one standalone question over a bounded subset of research-paper evidence.

    User question:
    {{ question }}

    Context label:
    {{ context_label }}

    Evidence context:
    ---
    {{ evidence_context }}
    ---

    Rules:
    - Use only the evidence context in this exact message.
    - Do not use outside knowledge.
    - Answer for this context only, not for papers outside this context.
    - Include direct quotes for every substantive claim.
    - Use at most 4 evidence quotes total.
    - Each evidence quote must carry the paper_id, paper_title if available, page if available, and chunk_id from the context metadata.
    - If the evidence context does not support an answer, set answer to exactly "Not enough evidence.", confidence to HIGH, and evidence to [].

    {{ ctx.output_format }}
  "#
}

function SynthesizeQuestionAnswers(
  question: string,
  intermediate_answers: string
) -> PaperQuestionAnswer {
  client Gateway
  prompt #"
    You synthesize intermediate answers from multiple research-paper contexts.

    User question:
    {{ question }}

    Intermediate answers with evidence:
    ---
    {{ intermediate_answers }}
    ---

    Rules:
    - Use only the intermediate answers and evidence in this exact message.
    - Do not use outside knowledge.
    - Synthesize across papers when the question asks for counts, comparisons, common trends, or whole-table summaries.
    - Preserve uncertainty and missing evidence instead of overclaiming.
    - Include direct quotes for the most important substantive claims.
    - Use at most 6 evidence quotes total.
    - Each evidence quote must carry paper_id, paper_title if available, page if available, and chunk_id from the intermediate evidence.
    - If the intermediate answers do not support an answer, set answer to exactly "Not enough evidence.", confidence to HIGH, and evidence to [].

    {{ ctx.output_format }}
  "#
}
