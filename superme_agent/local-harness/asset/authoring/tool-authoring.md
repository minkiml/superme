---
name: tool-authoring
description: The bar for an agent-facing tool: naming, description, and input schema. Pull before adding, renaming, or rewriting any tool a model calls.
enabled: true
hub-only: true
---

# Best practice for defining tools

- **Tools are for agents (LLMs), not humans.**
- See the official Claude docs for defining tools:
  `https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools`

## Naming

- **Use snake case.** Lowercase letters with underscores (e.g. `calculate_tax_rate`). This is the
  industry standard for LLM tool calling.
- **Lead with an action verb** that names the exact operation (`search_`, `create_`, `update_`,
  `calculate_`).
- **Follow the verb with a specific noun** — the exact target entity (`search_knowledge_base`, not
  `search_base`).
- **Keep it under 3–4 words.** Aim for 15 to 30 characters while remaining entirely distinct.
- **Avoid generic verbs.** Names like `run()`, `process()`, `execute()` or `manage()` tell Claude
  nothing about what they do.
- **Avoid internal jargon and abbreviations.** Do not name a tool `fetch_sub_acct_v2_cb()`. Use
  descriptive, universal terms.
- **Avoid overlapping names.** Never ship two tools as close as `get_user_data()` and
  `fetch_user_info()`. Claude will constantly mix them up.
- **Avoid uppercase or mixed casing.** Not `GetCustomerData`, not `get-customer-data`. Use one
  convention across the entire toolset.

## Descriptions

- **Explicit boundaries.** State when the tool should be used and when it should be avoided.
- **Negative constraints.** Say what the tool cannot handle, so Claude does not fall back to it.
  If you fail to say "do not use this for credit card updates", Claude will try it when no better
  alternative exists.
- **Input and output expectations.** Note specific formats or assumptions in the description text.
- **No implementation details.** Describe what the tool achieves, never its back-end logic. Claude
  does not need to know whether the backend is PostgreSQL, Redis or a Lambda function.
- **Never write short, single-word, or ambiguous definitions.** "Fetches weather" leaves Claude to
  guess, and it will guess wrong.
- **Name and description carry equal weight.** Claude does not assume a tool named
  `get_user_info` fetches a specific database record unless the description says so.

The examples below are JSON tool declarations. The same principles apply to the Tool Runner SDK
abstraction.

**Good example**
```json
"description": "Retrieves comprehensive customer account details by ID. Use this tool only when the user explicitly asks for profile information, billing status, or historical data. Do not use this tool for tracking active shipments or resetting passwords. Returns a JSON object with active subscriptions."
```

**Bad example**
```json
"description": "Fetches customer data."
```

## Input schemas

- **Flatten hierarchies.** Claude handles flat parameters and shallow objects far more reliably
  than deep nesting, which raises the chance it misses a mandatory parameter or misaligns a value.
- **No recursive schemas.** Self-referencing properties (`$ref`) are unsupported or error-prone.
- **Strict validation.** Add `"additionalProperties": false` inside object definitions so Claude
  cannot hallucinate arguments.
- **Describe every parameter.** Even one named `start_date` needs its exact expected format
  (`YYYY-MM-DD`) spelled out.
- **Keep each parameter description concise.** Clear and on point, never verbose.
- **Provide `input_examples`** where they help. Optional.

**Good example**
```json
{
  "name": "calculate_mortgage_payment",
  "description": "Calculates monthly mortgage payments based on principal, interest rate, and term duration. Use this tool exclusively for residential housing loan estimates.",
  "input_schema": {
    "type": "object",
    "properties": {
      "loan_principal": {
        "type": "number",
        "description": "The total amount of money borrowed. Must be a positive integer greater than 0. Do not pass currency symbols."
      },
      "annual_interest_rate": {
        "type": "number",
        "description": "The yearly interest rate expressed as a float. For example, pass 5.5 for a 5.5% rate, not 0.055."
      },
      "term_years": {
        "type": "integer",
        "enum": [15, 30],
        "description": "The duration of the loan in years. Strictly restricted to standard 15-year or 30-year terms."
      }
    },
    "required": ["loan_principal", "annual_interest_rate", "term_years"],
    "additionalProperties": false
  },
  "input_examples": [
    {
      "loan_principal": 350000,
      "annual_interest_rate": 6.25,
      "term_years": 30
    }
  ]
}
```
