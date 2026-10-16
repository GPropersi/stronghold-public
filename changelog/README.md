# Changelog

One file per day, named `MM-DD-YYYY-changelog.md`, with a first line `# Changelog — MM/DD/YYYY` and one
entry per action:

```
- [HH:MM] action: one or two line summary
```

Never edit these by hand. `.claude/scripts/changelog-add.sh "<action>: <summary>"` creates the day's file
and header and stamps the real time. Skills call it after planning, coding or git work.
