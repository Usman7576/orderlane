# Dataset Collection Form

How to collect real order messages from friends for the OrderLane evaluation.

**What you need:** 5–10 friends, about 10–15 minutes each.
**Typed messages** are collected in a Google Form. **Voice notes** are sent to you on WhatsApp, because Google Forms file uploads make people sign in and use your Drive storage.

The tasks are in [`evals/datasets/tasks.yaml`](../evals/datasets/tasks.yaml). Each task already has its correct answer, so you only have to check that the person did what the task asked.

There are two versions, **Form A** and **Form B**, with different tasks. Send Form A to half your friends and Form B to the other half. That way you get more variety without making the form long.

---

## Step 1: Create the Google Form (about 20 minutes)

Go to forms.google.com → Blank form. Build **Form A** as below, then use **More (⋮) → Make a copy** and swap in the Form B tasks.

**Settings (gear icon):**
- Responses → **Collect email addresses: Do not collect** (keeps it anonymous).
- Responses → **Limit to 1 response: off** (so nobody has to sign in).
- Presentation → **Show progress bar: on**.

### Form title and description

**Title:** `Help test my shop-ordering project (10 min)`

**Description** (paste as-is):

> Hi! I'm building a student project that reads shop orders sent on WhatsApp and turns them into proper orders.
>
> Imagine you run a small shop and you're ordering stock from your wholesaler on WhatsApp. For each task, **type the message exactly how you'd really send it**: any language mix (English, Roman Urdu…), short forms, spelling mistakes, all fine. **Please don't copy the product names from the task.** Write them however you'd normally say them.
>
> Don't include any real names, phone numbers or addresses.

### Section 1: About you

1. **Respondent code** (Short answer, required)
   Description: `Any nickname or 2–3 letters. It's only used to group your answers. Don't use your real name.`
2. **Which languages do you usually use when texting a shop?** (Checkboxes, required)
   Options: `English` · `Roman Urdu (Urdu in English letters)` · `Urdu script` · `Roman Hindi` · `Other`
3. **Consent** (Checkboxes, required)
   Option: `I agree my typed messages can be included in a public evaluation dataset for this portfolio project. No names or numbers will be published.`

### Section 2: Typed tasks

For **each text task** in your form's list below, add a **Paragraph** question:
- **Question title:** the task ID and instruction, e.g. `A01. Order 5 cartons of the big (950 gram) Tapal Danedar tea.`
- **Required:** on.
- For tasks with **2 messages** (A04, A05, A09, B03, B04), add this description: `Write each message on its own line, as if you sent them one after another.`

**Form A text tasks:** A01–A09
**Form B text tasks:** B01–B08

Copy each instruction exactly from `tasks.yaml`.

### Section 3: Voice notes

Add a **Title and description** block (not a question) with this text, filling in your own WhatsApp number:

> **Voice notes:** please send these to me on WhatsApp at **[your number]**.
> Before each voice note, type the task number (for example "A10"), then record the voice note.
> Speak naturally, as if you were calling in an order. Background noise is fine.
>
> [Paste the voice tasks for this form here: A10–A12 for Form A, or B09–B12 for Form B]

Then add one more question:

4. **May your voice recordings be published in the public project repository?** (Multiple choice, required)
   Options: `Yes, they can be public` · `No, use them only for private testing`

---

## Step 2: Message to send your friends

> Hey! Could you help with my final-year portfolio project? It takes about 10 minutes. You pretend to be a shopkeeper ordering stock on WhatsApp: type a few orders in a form, and send me 3–4 short voice notes. Type the way you normally would, with typos and short forms. That's exactly what I need. 🙏
> [form link]

---

## Step 3: After responses come in

1. **Export the typed answers:** in the form, Responses → **Link to Sheets**. Download it as CSV into `evals/datasets/raw/` (this folder is gitignored, because raw answers may contain personal details).
2. **Save the voice notes:** in WhatsApp, open each chat → ⋮ → More → **Export chat** → **Include media**. Copy the `.opus` files into:
   - `evals/datasets/audio/public/` if the person said **Yes** to publishing,
   - `evals/datasets/audio/private/` if they said **No** (this folder is gitignored).
   Rename each file `<respondent>_<task>.opus`, for example `zk_A10.opus`.
3. **Check each answer against its task.** If someone ordered something different from the task (for example 4 cartons instead of 5), change the gold answer for that case, not the message. A Day 2 script will turn the responses into `cases.jsonl` with the gold answers already filled in.
4. **Write reference transcripts** for the voice notes: type exactly what was said. These measure transcription accuracy separately from extraction accuracy.

## Privacy checklist
- [ ] No real names, phone numbers or addresses in the published data (check the typed answers).
- [ ] Voice notes from people who said **No** stay in `audio/private/` and are never committed.
- [ ] The README states the dataset was written by volunteers following scripted tasks.
