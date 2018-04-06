# Contributing guidelines

This file provides guidance for developers and code reviewers that work on
`skt`.

## Bugs

Please report all bugs using [GitHub
Issues](https://github.com/RH-FMK/skt/issues/new) within the `skt`
repository.

## Submitting patches

All patches should be submitted via GitHub's pull requests. When submitting
a patch, please do the following:

* Limit the first line of your commit message to 50 characters
* Describe the bug you found or the feature that is missing from the project
* Describe how your patch fixes the bug or improves the project
* Monitor the results of the CI jobs when you submit the pull request and fix
  any issues found in those tests

Code quality guidelines are available in the
[rh-fmk/meta repository](https://github.com/RH-FMK/meta/blob/master/CODING.md).

## Reviewing patches

Code reviewers must maintain the code quality within the project and review
patches on a regular basis. Reviewers should:

* Provide timely feedback for patches
* Feedback should be constructive (*"I would suggest that you..."* rather
  than *"I don't like this"*)
* Identify areas for improvement, especially with test coverage
