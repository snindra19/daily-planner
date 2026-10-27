A person added a medicine without saying when to take it. Suggest which meals to take it after and the spacing between doses. This is labeled as a suggestion, and the medicine is not scheduled until the user confirms it, ideally after checking with their doctor.

Use the dose count the user gave. Set `doses[].after` to a meal rule id such as `breakfast` or `dinner`, taken from `context.confirmed_rules` when those rules exist. Spread doses across the day. Do not recommend a medical dosage.
