## What changed and why

## How it was tested

## Risk
- [ ] Changes data (SQL that writes, deletes or alters; jobs that write to S3 or databases)
- [ ] Touches payment, participant or personal data
- [ ] Changes infrastructure, permissions or credentials handling
- [ ] Adds a dependency (name and reason):

## Checklist
- [ ] No passwords, keys, certificates or real data in this pull request
- [ ] Risky SQL in the SQL risk report is intended and explained above
- [ ] I have read and understood any AI-generated code in this pull request
