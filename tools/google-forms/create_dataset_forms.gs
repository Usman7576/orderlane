/**
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

const DATA = {
  "A": {
    "text": [
      {
        "id": "A01",
        "instruction": "Order 5 cartons of the big (950 gram) Tapal Danedar tea.",
        "multi": false
      },
      {
        "id": "A02",
        "instruction": "Order 2 dozen half-kilo Surf Excel packs and 1 dozen Lux soap.",
        "multi": false
      },
      {
        "id": "A03",
        "instruction": "Order 3 tins of 5-litre Dalda cooking oil and 1 carton of 1-litre Dalda oil pouches.",
        "multi": false
      },
      {
        "id": "A04",
        "instruction": "Send your order as 2 separate messages. First message: 10 bags of 10 kg Sunridge atta. Second message: add 2 bags (50 kg each) of sugar.",
        "multi": true
      },
      {
        "id": "A05",
        "instruction": "Order 4 cartons of Olpers 1-litre milk. Then send a second message changing it to 6 cartons.",
        "multi": true
      },
      {
        "id": "A06",
        "instruction": "Ask the shop the price of 1 kg Dalda ghee.",
        "multi": false
      },
      {
        "id": "A07",
        "instruction": "Order 1 box each of small Lay's masala chips, Kurkure and Slanty.",
        "multi": false
      },
      {
        "id": "A08",
        "instruction": "Order 2 dozen Shan biryani masala, 1 dozen Shan qorma masala, 2 dozen National salt, 1 dozen National red chilli powder and 1 dozen National haldi, all in one message.",
        "multi": false
      },
      {
        "id": "A09",
        "instruction": "Order 2 cartons of 1.5-litre Pepsi. Then send another message cancelling the whole order.",
        "multi": true
      }
    ],
    "voice": [
      {
        "id": "A10",
        "instruction": "Voice note ordering 3 cartons of 475 gram Tapal Danedar and 2 dozen Colgate toothpaste."
      },
      {
        "id": "A11",
        "instruction": "Voice note ordering 5 bags of 5 kg Guard basmati rice and 2 cartons of 1.5-litre Nestle water."
      },
      {
        "id": "A12",
        "instruction": "Voice note ordering 1 carton of 1-litre Habib oil, 1 carton of Sufi ghee and 1 dozen Harpic."
      }
    ]
  },
  "B": {
    "text": [
      {
        "id": "B01",
        "instruction": "Order 24 bars of Lifebuoy soap. Write the number of bars, not dozens.",
        "multi": false
      },
      {
        "id": "B02",
        "instruction": "Order half a carton of 1-litre Milkpak.",
        "multi": false
      },
      {
        "id": "B03",
        "instruction": "Order 2 cartons of 1.5-litre Coke. Then, in a new message, add 1 carton of Sprite and 1 carton of 7Up.",
        "multi": true
      },
      {
        "id": "B04",
        "instruction": "Order 1 dozen Sunsilk shampoo and 1 dozen Head & Shoulders shampoo. Then send a message removing the Sunsilk.",
        "multi": true
      },
      {
        "id": "B05",
        "instruction": "Ask whether Nido 390 gram is in stock.",
        "multi": false
      },
      {
        "id": "B06",
        "instruction": "Complain that yesterday's delivery had a leaking oil tin.",
        "multi": false
      },
      {
        "id": "B07",
        "instruction": "Order 10 packets of matches and 2 dozen Vim bars. Type it the way you would if you were in a hurry (short forms are fine).",
        "multi": false
      },
      {
        "id": "B08",
        "instruction": "Order 1 dozen Ariel detergent without saying the size.",
        "multi": false
      }
    ],
    "voice": [
      {
        "id": "B09",
        "instruction": "Voice note ordering 2 cartons of 950 gram Lipton Yellow Label and 1 dozen Tang."
      },
      {
        "id": "B10",
        "instruction": "Voice note, in your most natural style, ordering 3 bags of 5 kg Falak rice and 5 packets (1 kg each) of masoor daal."
      },
      {
        "id": "B11",
        "instruction": "Voice note asking how much money you currently owe the shop."
      },
      {
        "id": "B12",
        "instruction": "Voice note ordering 2 boxes of Sooper biscuit ticky packs, 2 boxes of Prince ticky packs and 1 dozen Rafhan jelly."
      }
    ]
  },
  "columns": {
    "respondent": "Respondent code",
    "languages": "Which languages do you usually use when texting a shop?",
    "consent": "Consent"
  }
};

const INTRO =
  "Hi! I'm building a student project that reads shop orders sent on WhatsApp and turns " +
  "them into proper orders.\n\n" +
  "Imagine you run a small shop and you're ordering stock from your wholesaler on " +
  "WhatsApp. For each task, type the message exactly how you'd really send it: any " +
  "language mix (English, Roman Urdu...), short forms and spelling mistakes are all fine. " +
  "Please don't copy the product names from the task; write them however you'd normally " +
  "say them.\n\nDon't include any real names, phone numbers or addresses.";

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
  Logger.log(links.join("\n"));
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
    .join("\n");
  form.addPageBreakItem()
    .setTitle("Voice notes")
    .setHelpText(
      "Please send these as WhatsApp voice notes to " + WHATSAPP_NUMBER + ". Before each one, " +
        "type the task number (for example " + tasks.voice[0].id + "), then record. Speak " +
        "naturally, as if you were calling in an order. Background noise is fine.\n\n" +
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
