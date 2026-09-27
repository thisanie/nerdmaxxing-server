# NerdMaxxing Challenge Template

Fill out one copy of this document for each challenge.

## 1. Basic Information

**Challenge title:**

**Short description:**

Write one or two sentences. Maximum 300 characters.

**Full description:**

Explain what the participant will learn or do, how they should approach it, and exactly when the challenge is complete.

**Image URL:**

Optional. Use a direct HTTPS image URL.

**Difficulty:**

Choose one: `BEGINNER`, `INTERMEDIATE`, `ADVANCED`

**Category:**

Choose up to three:

- Brain & Memory
- Technology
- Games & Strategy
- Knowledge
- Creative
- Music
- Languages
- Physical
- Science
- Practical

## 2. Goal and Measurement

**What exactly is the participant trying to achieve?**

**Metric key:**

Use lowercase letters, numbers, and underscores only. Examples: `hold_duration`, `solve_time`, `books_completed`.

**Metric label:**

Human-readable name, such as `Freestanding hold`.

**Metric kind:**

Examples: `DURATION`, `DISTANCE`, `REPETITIONS`, `COUNT`, `PERCENTAGE`, `BOOLEAN`.

**Unit:**

Examples: `seconds`, `minutes`, `kilometers`, `repetitions`, `books`, `days`.

**Target value:**

Use a number or boolean.

**Target direction:**

Choose one: `AT_LEAST`, `AT_MOST`, `EXACTLY`, `BOOLEAN`.

**Is this the primary metric?**

Usually `yes`. Only one metric may be primary.

**Display format:**

Examples: `INTEGER`, `DECIMAL_2`, `PERCENTAGE`.

## 3. Completion Requirement

**Requirement statement:**

Write the exact measurable requirement.

**Requirement operator:**

Usually the same as the target direction: `AT_LEAST`, `AT_MOST`, `EXACTLY`, or `BOOLEAN`.

**Requirement value:**

**Requirement unit:**

Must match the metric unit.

**Requirement label:**

A clear sentence describing the requirement.

## 4. Verification

**Verification type:**

Choose one: `SELF_REPORTED`, `VIDEO_VERIFIED`, `ACCOUNT_VERIFIED`.

**Required successful attempts:**

Usually `1` or `3`.

**Verification instructions:**

Explain exactly what evidence the participant must submit.

Include what must be visible, whether continuous recording is required, whether edits are allowed, what starts the attempt, what ends or invalidates it, and whether external tools or assistance are allowed.

## 5. Effort and Duration

**Minimum effort in minutes:**

**Maximum effort in minutes:**

**Expected duration in days:**

## 6. Resources

Add at least one resource. Copy this section for additional resources.

### Resource 1

**Title:**

**URL:**

**Resource type:**

Choose one: `ARTICLE`, `VIDEO`, `COURSE`, `TOOL`, `COMMUNITY`, `DOCUMENT`, `INTERACTIVE_GUIDE`, `INTERACTIVE_TRAINER`.

**Why is this resource useful?**

**Required for completion:**

`yes` or `no`

## 7. Milestones

Milestones should represent meaningful stages of progress. Copy this section for additional milestones.

### Milestone 1

**Title:**

**Description:**

**Target value:**

**Resource numbers used:**

Use resource numbers as written in this document. Example: `1, 2`.

## Notes for Conversion to JSON

- Resource indexes in JSON start at `0`, not `1`.
- Use numbers for numeric values, not quoted strings.
- Use `true`, `false`, or `null` for boolean and empty values.
- Metric keys must match the metric key used by requirements.
- Requirement units must match metric units.
- Only one metric may have `is_primary: true`.
- Do not invent IDs, slugs, creator IDs, timestamps, statuses, or visibility values. The seed process generates those.
