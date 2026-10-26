"""Generate tools/google-forms/create_dataset_forms.gs from evals/datasets/tasks.yaml.

Question titles come from the importer's constants, so form exports always import cleanly.
Re-run after changing tasks.yaml (from backend/):
    uv run python -m scripts.generate_google_forms
"""

import json
import sys
from pathlib import Path

import yaml

from evals.import_form import CONSENT_COLUMN, LANGUAGES_COLUMN, RESPONDENT_COLUMN

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "tools" / "google-forms" / "create_dataset_forms.gs"
tasks = yaml.safe_load((ROOT / "evals/datasets/tasks.yaml").read_text(encoding="utf-8"))


def form_tasks(form: str) -> dict[str, list[dict[str, object]]]:
    text, voice = [], []
    for t in tasks:
        if t["form"] != form:
            continue
        item = {"id": t["id"], "instruction": " ".join(str(t["instruction"]).split())}
        if t["channel"] == "text":
            item["multi"] = len(t["gold"]["intents"]) > 1
            text.append(item)
        else:
            voice.append(item)
    return {"text": text, "voice": voice}


data = {
    "A": form_tasks("A"),
    "B": form_tasks("B"),
    "columns": {
        "respondent": RESPONDENT_COLUMN,
        "languages": LANGUAGES_COLUMN,
        "consent": CONSENT_COLUMN,
    },
}

script = (
    """/**
 * OrderLane: creates the two dataset forms (A and B) and one Google Sheet that collects
 * every response. GENERATED from evals/datasets/tasks.yaml; do not edit by hand.
 *
 * How to use (about 2 minutes):
 *   1. Open https://script.google.com and click "New project".
 *   2. Delete the sample code, paste this whole file, and click Save.
 *   3. Change WHATSAPP_NUMBER below to the number friends should send voice notes to.
 *   4. Choose the function "createDatasetForms" in the toolbar and click Run.
 *   5. Allow access when Google asks (it's your own script). If you see "Google hasn't
 *      verified this app", click Advanced, then "Go to ... (unsafe)". This only lets the
 *      script create forms and a sheet in your own Drive.
 *   6. Open "Execution log": it prints the two form links to send and the sheet link.
 *
 * Send Form A to half of your friends and Form B to the other half.
 */

const WHATSAPP_NUMBER = "+92 3XX XXXXXXX"; // <- your WhatsApp number for voice notes

const DATA = """
    + json.dumps(data, indent=2, ensure_ascii=False)
    + """;

const INTRO =
  "Hi! I'm building a student project that reads shop orders sent on WhatsApp and turns " +
  "them into proper orders.\\n\\n" +
  "Imagine you run a small shop and you're ordering stock from your wholesaler on " +
  "WhatsApp. For each task, type the message exactly how you'd really send it: any " +
  "language mix (English, Roman Urdu...), short forms and spelling mistakes are all fine. " +
  "Please don't copy the product names from the task; write them however you'd normally " +
  "say them.\\n\\nDon't include any real names, phone numbers or addresses.";

function createDatasetForms() {
  const sheet = SpreadsheetApp.create("OrderLane dataset responses");
  const links = [];
  ["A", "B"].forEach(function (version) {
    const form = buildForm_(version, DATA[version]);
    form.setDestination(FormApp.DestinationType.SPREADSHEET, sheet.getId());
    links.push("Form " + version + " (send to friends): " + form.getPublishedUrl());
    links.push("Form " + version + " (edit): " + form.getEditUrl());
  });
  links.push("Responses sheet: " + sheet.getUrl());
  Logger.log(links.join("\\n"));
}

function buildForm_(version, tasks) {
  const form = FormApp.create("Help test my shop-ordering project (Form " + version + ")");
  form.setDescription(INTRO);
  form.setCollectEmail(false);
  form.setLimitOneResponsePerUser(false);
  form.setProgressBar(true);
  form.setShowLinkToRespondAgain(false);
  form.setConfirmationMessage(
    "Thank you! Don't forget the voice notes on WhatsApp if you haven't sent them yet."
  );

  // About you
  form.addTextItem()
    .setTitle(DATA.columns.respondent)
    .setHelpText("Any nickname or 2-3 letters. It only groups your answers. Not your real name.")
    .setRequired(true);
  form.addCheckboxItem()
    .setTitle(DATA.columns.languages)
    .setChoiceValues([
      "English",
      "Roman Urdu (Urdu in English letters)",
      "Urdu script",
      "Roman Hindi",
      "Other",
    ])
    .setRequired(true);
  form.addCheckboxItem()
    .setTitle(DATA.columns.consent)
    .setChoiceValues([
      "I agree my typed messages can be included in a public evaluation dataset for this " +
        "portfolio project. No names or numbers will be published.",
    ])
    .setRequired(true);

  // Typed tasks
  form.addPageBreakItem()
    .setTitle("Typed orders")
    .setHelpText("Write each message the way you'd send it on WhatsApp.");
  tasks.text.forEach(function (task) {
    const item = form.addParagraphTextItem()
      .setTitle(task.id + ". " + task.instruction)
      .setRequired(true);
    if (task.multi) {
      item.setHelpText(
        "Write each message on its own line, as if you sent them one after another."
      );
    }
  });

  // Voice notes (sent on WhatsApp, because form uploads need a Google sign-in)
  const voiceList = tasks.voice
    .map(function (task) { return task.id + ": " + task.instruction; })
    .join("\\n");
  form.addPageBreakItem()
    .setTitle("Voice notes")
    .setHelpText(
      "Please send these as WhatsApp voice notes to " + WHATSAPP_NUMBER + ". Before each one, " +
        "type the task number (for example " + tasks.voice[0].id + "), then record. Speak " +
        "naturally, as if you were calling in an order. Background noise is fine.\\n\\n" +
        voiceList
    );
  form.addMultipleChoiceItem()
    .setTitle("May your voice recordings be published in the public project repository?")
    .setChoiceValues([
      "Yes, they can be public",
      "No, use them only for private testing",
    ])
    .setRequired(true);
  return form;
}
"""
)

out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(script, encoding="utf-8", newline="\n")
print(
    f"wrote {out} ({len(data['A']['text'])}+{len(data['A']['voice'])} A tasks, "
    f"{len(data['B']['text'])}+{len(data['B']['voice'])} B tasks)"
)
