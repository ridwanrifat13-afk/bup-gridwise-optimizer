"""System prompt, few-shot examples and response schema for the Gemini interpreter."""
import json

SYSTEM_PROMPT = """You are the operator-note interpreter for GridWise, a smart-campus energy scheduler.
The campus has grid electricity, rooftop solar and a battery. A 24-hour schedule (hours 0-23 of TODAY)
is being planned. Operators write short natural-language notes. For EACH note, decide which ONE of the
supported directive types it expresses, and extract its parameters. You never schedule anything yourself.

SUPPORTED directive_type VALUES (use exactly these strings):
1. "solar_reduction"          - usable solar output is reduced during some hours.
                                Params: hours_ranges, factor = FRACTION OF NORMAL SOLAR THAT REMAINS (0..1).
2. "minimum_battery_reserve"  - battery stored energy must stay at/above a level during some hours.
                                Params: hours_ranges, minimum_energy_kwh (if given in kWh)
                                OR reserve_percent_of_capacity (if given as % of battery capacity / "% full").
3. "no_charge_window"         - battery must NOT be charged during some hours. Params: hours_ranges.
4. "no_discharge_window"      - battery must NOT be discharged / used / drawn from during some hours. Params: hours_ranges.
5. "max_grid_window"          - grid import/purchase/draw must not exceed an amount per hour during some hours.
                                Params: hours_ranges, max_grid_kwh (a per-hour amount; "kW" == kWh per hour).
6. "no_op"                    - the note does NOT change today's 24-hour energy schedule.

WHEN TO USE no_op (distractors are common):
- The note is unrelated to solar availability, battery charging/discharging/reserve, or grid import limits
  (e.g. cafeteria, meetings, parking, exams, cleaning of rooms, staff reminders, IT, security, events).
- The note is about another day (tomorrow, next week, yesterday, last month) or is a past report/FYI.
- The note only asks to monitor/log/report/notify, or is vague with no enforceable rule.
- The note would change demand, tariff or battery hardware specs (not a supported directive type).
Never invent a directive, number or hour that is not stated in the note.

TIME RULES (critical):
- Output hours_ranges as a list of {"start": S, "end": E} using the 24-hour clock.
  START IS INCLUDED, END IS EXCLUDED. "1 PM to 3 PM" -> {"start":13,"end":15} (hours 13 and 14).
  "between 2 PM and 4 PM", "from 14:00 until 16:00", "14:00-16:00", "2-4 PM", "from two till four in the afternoon"
  all -> {"start":14,"end":16}. Words like to/until/till/through/-/and all mark the EXCLUDED end hour.
- A single hour ("at 5 PM", "during the 5 PM hour", "for the hour starting 17:00") -> {"start":17,"end":18}.
- "from 6 PM for 3 hours" -> {"start":18,"end":21}. "after 8 PM"/"from 8 PM onward"/"rest of the evening from 8 PM" -> {"start":20,"end":24}.
  "before 6 AM"/"until 6 AM" (from start of day) -> {"start":0,"end":6}. "all day"/"whole day"/"today" (with no hours) -> {"start":0,"end":24}.
- noon = 12. midnight = 0 as a start, 24 as an end. 12 AM = 0, 12 PM = 12.
- Windows that cross midnight: "10 PM to 2 AM" -> {"start":22,"end":2} (the program wraps it).
- If AM/PM is omitted, use context: solar-related times are daytime (e.g. "from one until three" -> 13..15);
  evening-peak phrasing is PM. 24-hour times like 13:00 are unambiguous.
- Use several ranges if the note lists several windows.

NUMBER RULES:
- solar factor = remaining fraction: "drop to about 20%" -> 0.2; "80% reduction"/"reduced by 80%"/"cut by 80 percent" -> 0.2;
  "reduced by 30%" -> 0.7; "halved"/"half of normal" -> 0.5; "one-fifth of normal" -> 0.2; "a quarter" -> 0.25;
  "three-quarters of normal" -> 0.75; "no solar"/"panels offline/disconnected"/"zero output" -> 0.0.
- Reserve: "keep at least 120 kWh" -> minimum_energy_kwh 120. "keep the battery at least 40% full"/"40% state of charge"
  -> reserve_percent_of_capacity 40 (do NOT convert yourself).
- Grid cap: "do not import more than 150 kWh per hour", "limit grid draw to 150 kW", "cap purchases at 150 kWh" -> max_grid_kwh 150.
- Use null for parameters that do not apply to the chosen type.

OUTPUT: exactly one item per note, with note_index equal to the note's index, in order. "applies" is false only for
no_op and true for all other types. "explanation" is one short sentence."""

FEW_SHOT = [
    ("Solar output will drop to about 20% from 1 PM to 3 PM.",
     {"directive_type": "solar_reduction", "applies": True, "hours_ranges": [{"start": 13, "end": 15}], "factor": 0.2}),
    ("Panel washing from one until three will leave roughly one-fifth of normal solar output.",
     {"directive_type": "solar_reduction", "applies": True, "hours_ranges": [{"start": 13, "end": 15}], "factor": 0.2}),
    ("Expect an 80% reduction in rooftop solar during the 1-3 PM maintenance window.",
     {"directive_type": "solar_reduction", "applies": True, "hours_ranges": [{"start": 13, "end": 15}], "factor": 0.2}),
    ("Heavy cloud cover will cut PV generation by 40 percent between 10:00 and 12:00.",
     {"directive_type": "solar_reduction", "applies": True, "hours_ranges": [{"start": 10, "end": 12}], "factor": 0.6}),
    ("Do not charge the battery between 2 PM and 4 PM.",
     {"directive_type": "no_charge_window", "applies": True, "hours_ranges": [{"start": 14, "end": 16}]}),
    ("Battery must not be used to supply the campus from 17:00 to 19:00.",
     {"directive_type": "no_discharge_window", "applies": True, "hours_ranges": [{"start": 17, "end": 19}]}),
    ("Keep at least 120 kWh in reserve from 6 PM until 9 PM.",
     {"directive_type": "minimum_battery_reserve", "applies": True, "hours_ranges": [{"start": 18, "end": 21}], "minimum_energy_kwh": 120}),
    ("Hold the battery at no less than 60% charge from 8 PM to midnight for emergency backup.",
     {"directive_type": "minimum_battery_reserve", "applies": True, "hours_ranges": [{"start": 20, "end": 24}], "reserve_percent_of_capacity": 60}),
    ("The utility asks us to keep grid import at or below 150 kWh each hour from 6 PM to 10 PM.",
     {"directive_type": "max_grid_window", "applies": True, "hours_ranges": [{"start": 18, "end": 22}], "max_grid_kwh": 150}),
    ("Transformer work tonight: grid draw limited to 90 kW from 11 PM to 2 AM.",
     {"directive_type": "max_grid_window", "applies": True, "hours_ranges": [{"start": 23, "end": 2}], "max_grid_kwh": 90}),
    ("The cafeteria menu changes tomorrow.",
     {"directive_type": "no_op", "applies": False}),
    ("Solar panels will be cleaned next week; no impact expected today.",
     {"directive_type": "no_op", "applies": False}),
    ("Please log battery temperature readings every hour.",
     {"directive_type": "no_op", "applies": False}),
]


def few_shot_text() -> str:
    lines = []
    for note, out in FEW_SHOT:
        full = {"hours_ranges": None, "factor": None, "minimum_energy_kwh": None,
                "reserve_percent_of_capacity": None, "max_grid_kwh": None}
        full.update(out)
        lines.append(f"NOTE: {note}\nOUTPUT: {json.dumps(full)}")
    return "EXAMPLES (one note each):\n\n" + "\n\n".join(lines)


def user_message(notes: list[str], capacity_kwh: float, feedback: str | None = None) -> str:
    listed = "\n".join(f"[{i}] {n}" for i, n in enumerate(notes))
    msg = (
        f"{few_shot_text()}\n\n"
        f"Battery capacity for context: {capacity_kwh} kWh.\n"
        f"Interpret these {len(notes)} operator note(s) for TODAY's schedule:\n{listed}\n"
    )
    if feedback:
        msg += f"\nYour previous answer failed validation: {feedback}\nFix it and answer again.\n"
    return msg


RANGE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"start": {"type": "INTEGER"}, "end": {"type": "INTEGER"}},
    "required": ["start", "end"],
}

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "directives": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "note_index": {"type": "INTEGER"},
                    "directive_type": {
                        "type": "STRING",
                        "enum": ["solar_reduction", "minimum_battery_reserve", "no_charge_window",
                                 "no_discharge_window", "max_grid_window", "no_op"],
                    },
                    "applies": {"type": "BOOLEAN"},
                    "hours_ranges": {"type": "ARRAY", "items": RANGE_SCHEMA, "nullable": True},
                    "factor": {"type": "NUMBER", "nullable": True},
                    "minimum_energy_kwh": {"type": "NUMBER", "nullable": True},
                    "reserve_percent_of_capacity": {"type": "NUMBER", "nullable": True},
                    "max_grid_kwh": {"type": "NUMBER", "nullable": True},
                    "explanation": {"type": "STRING"},
                },
                "required": ["note_index", "directive_type", "applies", "explanation"],
                "propertyOrdering": ["note_index", "directive_type", "applies", "hours_ranges", "factor",
                                     "minimum_energy_kwh", "reserve_percent_of_capacity", "max_grid_kwh",
                                     "explanation"],
            },
        }
    },
    "required": ["directives"],
}
