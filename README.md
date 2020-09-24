# gds-pre-commit Instructions

> NOTE: We used to vendor our own bundle of pre-commit and detect-secrets. This was too brittle for us to support, we now recommend installing the off-the-shelf tools, as detailed below:

Detect Secrets is a tool for preventing secrets being committed to git (https://github.com/Yelp/detect-secrets).

You can add a pre-commit git hook to run detect-secrets automatically using the pre-commit framework.

Detect-secrets scans the contents of a commit to see if contains anything that looks sensitive, like an SSH key, an AWS key or an API token. If it detects something that looks like a “secret”, it prevents the commit from going through; otherwise everything works as normal.

This readme will guide you through installing it to your machine.

## Quick Install

First ensure you have Python 3 and pip installed.  On mac, run
`brew install python`; on ubuntu, run `apt-get install python3-pip`.


If using `pyenv`, make sure you've chosen a system-wide python executable, that will work no matter what you're working on.

Then install the Pre-commit Framework, as detailed here: https://pre-commit.com/#install.

We have created a `.pre-commit-config.yaml` with detect-secrets enabled in this repository.

## Ignoring secrets
1. In-line `# pragma: allowlist secret` In any language's comment format.
1. When running detect-secrets, with `detect-secrets scan --update .secrets.baseline --exclude-files go.sum`
1. Add to your repository's `.pre-commit-config.yaml`, eg:
```
-   repo: git@github.com:Yelp/detect-secrets
    rev: v0.14.3
    hooks:
    -   id: detect-secrets
        args: ['--baseline', '.secrets.baseline']
        exclude: .*/hashes/.*
```

## What Works And What Doesn't

There are many plugins enabled out of the box, the full list which includes slack tokens, aws keys, and stripe keys, are here: https://github.com/Yelp/detect-secrets/tree/master/detect_secrets/plugins

Most secrets we find are caught by the `high entropy strings` plugin, which checks the randomness of a string. Highly-random strings are very likely to be passwords or keys, the logic goes.

However this has a few surprising edge-cases:
 - The string `oaisntoiwentoiasrayufntoiarnf` won't trigger it despite a human thinking it looks 'random'. It's just lower case ascii. hardly random at all.
 - UUIDs aren't caught by it. Again, `27375774-a49e-4fa6-9cb1-c2fdcc43b9d6` looks random, and we might even use it as a fairly secure password, but it's just lower-case hex. Hardly random.
 - You may even find some short truly random strings slip by because they are short and accidentally don't have enough complexity.

## Further Reading

 - [Usage](usage.md)
 - [Useful Hooks and plugins](pre-commit-plugins.md)
