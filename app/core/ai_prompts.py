"""
Prompt templates and JSON schemas for the AI text parser.
One schema per context (opportunity, customer, activity, task, quote).
"""

# ---------------------------------------------------------------------------
# Shared preamble — every prompt gets this
# ---------------------------------------------------------------------------
SYSTEM_PREAMBLE = """You are a data extraction assistant for a CRM system.

The user will paste free-form text (email, meeting notes, website content,
mixed Persian and English). Your job is to extract structured data.

RULES:
1. Output ONLY valid JSON. No markdown fences, no commentary.
2. Extract ONLY what is clearly stated. Never invent data.
3. If a field is missing, omit it (don't guess, don't use "" or "N/A").
4. Preserve original language for names, notes, subjects — do NOT translate.
5. Dates: output as ISO YYYY-MM-DD Gregorian. If the source uses Persian
   calendar (e.g. 1405/06/20), convert to Gregorian. If a date is invalid
   or ambiguous, omit it and add a warning.
6. Numbers: value amounts must be numbers, not strings. "200 million" → 200000000.
7. If the text contains multiple entities (e.g. 2 contacts), return all of them.
8. If the text suggests an update to an existing record, set intent="update".
   If it suggests creating a new record, set intent="create".
"""


# ---------------------------------------------------------------------------
# Per-context schemas
# ---------------------------------------------------------------------------
CONTEXT_SCHEMAS = {

    # -----------------------------------------------------------------------
    'opportunity': {
        'description': 'Parse text into a new/updated Opportunity + Company + Contacts + Activity.',
        'schema': """
{
  "intent": "create" | "update",
  "entities": {
    "company": {
      "name": "string",
      "phones": [{"value": "string", "type": "Main|Sales|Fax|CompanyMobile"}],
      "website": "string (URL)",
      "address": "string",
      "email": "string"
    },
    "contacts": [
      {
        "first_name": "string",
        "last_name": "string",
        "job_title": "string",
        "phones": [{"value": "string", "type": "Mobile|DirectPhone"}],
        "email": "string",
        "linkedin": "string (URL)"
      }
    ],
    "opportunity": {
      "name": "string",
      "value": number,
      "currency": "USD|IRR|EUR",
      "probability": number (0-100),
      "stage": "Prospecting|Qualification|Proposal|Negotiation|Closed Won|Closed Lost",
      "expected_close_date": "YYYY-MM-DD",
      "notes": "string",
      "contact_person": "string (primary contact name)"
    },
    "activities": [
      {
        "activity_type": "Call|Meeting|Email|Note",
        "subject": "string",
        "activity_date": "YYYY-MM-DD",
        "reminder_date": "YYYY-MM-DD",
        "notes": "string"
      }
    ]
  },
  "warnings": ["string"]
}
""",
    },

    # -----------------------------------------------------------------------
    'customer': {
        'description': 'Parse text into a new Company (Customer) with phones and contacts.',
        'schema': """
{
  "intent": "create" | "update",
  "entities": {
    "company": {
      "name": "string",
      "phones": [{"value": "string", "type": "Main|Sales|Fax|CompanyMobile"}],
      "website": "string (URL)",
      "address": "string",
      "email": "string",
      "business_type": "string (e.g. Oil & Gas, Automotive, Steel)"
    },
    "contacts": [
      {
        "first_name": "string",
        "last_name": "string",
        "job_title": "string",
        "phones": [{"value": "string", "type": "Mobile|DirectPhone"}],
        "email": "string",
        "linkedin": "string (URL)"
      }
    ]
  },
  "warnings": ["string"]
}
""",
    },

    # -----------------------------------------------------------------------
    'activity': {
        'description': 'Parse text into one or more activity log entries.',
        'schema': """
{
  "intent": "create",
  "entities": {
    "activities": [
      {
        "activity_type": "Call|Meeting|Email|Note",
        "subject": "string",
        "activity_date": "YYYY-MM-DD",
        "reminder_date": "YYYY-MM-DD",
        "notes": "string",
        "opportunity_hint": "string (name of related opportunity if mentioned)"
      }
    ]
  },
  "warnings": ["string"]
}
""",
    },

    # -----------------------------------------------------------------------
    'task': {
        'description': 'Parse text into one or more tasks.',
        'schema': """
{
  "intent": "create",
  "entities": {
    "tasks": [
      {
        "subject": "string",
        "due_date": "YYYY-MM-DD",
        "priority": "Low|Medium|High",
        "notes": "string"
      }
    ]
  },
  "warnings": ["string"]
}
""",
    },

    # -----------------------------------------------------------------------
    'quote': {
        'description': 'Parse email/thread into a quote note + activity.',
        'schema': """
{
  "intent": "create" | "update",
  "entities": {
    "quote": {
      "subject": "string",
      "notes": "string"
    },
    "activity": {
      "activity_type": "Call|Meeting|Email|Note",
      "subject": "string",
      "activity_date": "YYYY-MM-DD",
      "notes": "string"
    }
  },
  "warnings": ["string"]
}
""",
    },
}


# ---------------------------------------------------------------------------
# Few-shot examples — teach the model with real-world patterns
# ---------------------------------------------------------------------------
FEW_SHOT = {
    'opportunity': """
EXAMPLE INPUT:
"I contacted Petrofanavr company, here is their contacts from website:
Tel: 021-88776655 (Main), 021-88776656 (Sales)
Website: petrofanavr.ir
Address: Tehran, Valiasr St.
I called Mr. Ahladi, who is sales engineer, mobile 0912-3456789.
Set a meeting on 2026-09-25 about the new ball valve project.
Budget is around 200 million tomans."

EXAMPLE OUTPUT:
{
  "intent": "create",
  "entities": {
    "company": {
      "name": "Petrofanavr",
      "phones": [
        {"value": "021-88776655", "type": "Main"},
        {"value": "021-88776656", "type": "Sales"}
      ],
      "website": "petrofanavr.ir",
      "address": "Tehran, Valiasr St."
    },
    "contacts": [
      {
        "first_name": "Ahladi",
        "job_title": "Sales Engineer",
        "phones": [{"value": "0912-3456789", "type": "Mobile"}]
      }
    ],
    "opportunity": {
      "name": "Ball Valve Project with Petrofanavr",
      "value": 200000000,
      "currency": "IRR",
      "notes": "Budget around 200 million tomans"
    },
    "activities": [
      {
        "activity_type": "Meeting",
        "subject": "Meeting with Mr. Ahladi",
        "activity_date": "2026-09-25",
        "notes": "About the new ball valve project"
      }
    ]
  },
  "warnings": []
}
""",
}


def build_prompt(context, user_text):
    """Return (system_prompt, user_prompt) for the given context."""
    ctx = CONTEXT_SCHEMAS[context]
    few_shot = FEW_SHOT.get(context, '')

    system_prompt = f"""{SYSTEM_PREAMBLE}

TASK: {ctx['description']}

OUTPUT JSON SCHEMA (follow exactly):
{ctx['schema']}

{few_shot}
"""

    user_prompt = f"""Extract structured CRM data from the following text.
Respond with JSON only.

--- BEGIN TEXT ---
{user_text}
--- END TEXT ---"""

    return system_prompt, user_prompt