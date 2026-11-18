---
name: characters
always: false
description: Use when an admin asks to list, add, change, enable, disable or remove characters, personas or voices, to change what a character speaks for, or asks why a character is or is not being used.
---

Characters are the voices the bot speaks in. Each one has a name, an optional song it follows, a sender name and icon, and the features and skills it is bound to. A character bound to `feature:pr_reminder` gives PR reminders their wording. A character bound to `skill:<name>` gives the agent replies for that skill their tone. A character bound to `skill:core` is the default voice of every task that has none of its own. A thread keeps the character it was given. These tools are admin-only.

## Tools

- `list_characters()` — every stored character with its song and artist, whether it is enabled, what it is bound to, when it last changed, and whether its row is valid. A row that is invalid, or disabled, is never used. Bindings to skills that do not exist are flagged.
- `add_character(name, display_name, bindings, song?, artist?, icon_emoji?, icon_url?, persona?, voices?, enabled?, dry_run?)` — stores a new character. A name that already exists is refused. `name`, `display_name` and at least one binding are required.
- `update_character(name, ..., dry_run?)` — changes only the fields given. `bindings` replaces the whole list, `add_bindings` and `remove_bindings` change single bindings, and `voices` replaces the voice block of each feature it names. The name cannot be changed.
- `remove_character(name)` — deletes a character.

## What a character needs

- A binding `feature:pr_reminder` needs `voices.pr_reminder`, with a `headers` list of 1 to 10 templates. Each header contains `{who}` and `{subject}` (or `{Subject}` at the start of a sentence). The other fields are optional and fall back to the default wording: `subject_one`, `subject_many` (with `{count}`), `flair_fresh`, `flair_stale`, `flair_ancient`, `author_court_with_waiting`, `author_court_alone`, `author_line`, `schedule_footer` and `expired` (with `{schedule_id}`), and `unreadable` (with `{schedule_id}` and `{error_code}`).
- A binding `skill:<name>` needs a `persona`: a short instruction for the tone, in English, that says how to sound and keeps facts, numbers and identifiers exact. The skill names are the ones the bot has, such as `metadata`, `jobs`, `lineage`, `billing`, `access`, `pr-reminders` and `core`.
- Texts must not contain `<`, `>`, `@channel`, `@here` or `@everyone`, and each template is at most 300 characters. Use only standard Slack emoji.
- `display_name` is the sender name shown in Slack. Use the song title, never an artist's name, and never write a line as if the artist said it. Do not quote lyrics.
- The name is a lower-case slug such as `em-gai-mua`.

## Rules

- **Validate before you store, every time.** Adding or changing a character always goes in these steps, in this order:
  1. **Collect.** Work out every required value from the admin's message: for a new character the name, the display name, what it speaks for (at least one binding), and the voice text each binding needs. If anything is missing or unclear, ask for it in one message that lists every gap, and wait. Do not invent a name, a binding or any text. You may draft the persona or the templates only when the admin asked you to, and then you show them.
  2. **Check.** Call the tool with `dry_run: true`. If it lists problems, tell the admin each one, propose a fix and go back to step 1 for what changed. Never call with `dry_run: false` while the check still lists a problem.
  3. **Confirm.** Show the admin the full definition, or the exact change, including any Vietnamese text and the result of the check, and ask whether to store it.
  4. **Store.** Only after a clear yes, call again with `dry_run: false` and the same values.
  For `remove_character`, name the character and wait for a clear yes. A change in scope (bindings) is a change like any other.
- Call `list_characters` when the admin asks what exists, which character speaks for a task, or why one is not used, and when the admin names a character you cannot find. Never guess a character name; show the names that exist.
- If the admin gives a voice in plain words ("sassy, mixes English"), write the persona and the templates yourself, check them, show them, and ask whether to store them. Keep the teasing on the PR or the task and never on a person.
- If a tool lists problems, relay every one as it is and offer a fix. Nothing was stored when a tool refuses, and a result that starts with "Valid. Nothing was stored yet" means the check passed and nothing has been stored.
- Report what the tool says: the name, whether it is enabled, what it is bound to, and any skill that does not exist.
- To stop using a character without losing it, use `update_character` with `enabled` false. Removing a character that ships with the bot brings it back the next time the bot is deployed, so offer disabling instead and say so.
- Giving a character `skill:core` changes the voice of every task without its own character, so say that before you do it.
