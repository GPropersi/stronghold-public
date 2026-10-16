# Plans

The central plans store: every plan, piece of research, review and design mock for every project lives
here, never inside the project repos. Skills such as `plan-creator`, `plan-reviewer` and `run-plan` read
and write this tree.

```
plans/<project>/
|-- open/<topic>/<name>.md        <- active plans; reviews/ research/ mocks/ beside each plan
|-- completed/<topic>/<name>.md   <- finished topics, moved as a whole folder
`-- research/                     <- standalone research not tied to a plan
```

`<project>` is the value of the `Plans bucket` key in that project's `## Claude Config` section. Every plan
starts with frontmatter naming its `repo:` and `branch:`.

This repo ships the layout only. Your plans accumulate here as you use the skills.
