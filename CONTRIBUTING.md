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
* Make sure the code keeps working and making sense after each commit, and
  does not require any further commits to fix it.
* Make sure each commit contains only one complete logical change, no more, no
  less.
* Always document each new module, class, or function.
* Add or update the documentation when the logic or the interface of an
  existing module, class, or function changes.
* Monitor the results of the CI jobs when you submit the pull request and fix
  any issues found in those tests
* Read review comments carefully, discuss the points you disagree with,
  and address all outstanding comments with each respin, so the comments are
  not lost and there's no backtracking.
* Reply to review comments and update the patches quickly, preferably within
  one day, so the reviewer has fresh memory of the code, and review finishes
  sooner.

Code quality guidelines are available in the
[rh-fmk/meta repository](https://github.com/RH-FMK/meta/blob/master/CODING.md).

## Reviewing patches

Code reviewers must maintain the code quality within the project and review
patches on a regular basis. Reviewers should:

* Strive to provide timely feedback for patches and respins, preferably
  replying within a day.
* Feedback should be constructive (*"I would suggest that you..."* rather
  than *"I don't like this"*)
* Identify areas for improvement, especially with test coverage
* Test the changes being reviewed with each respin.
* Provide as complete feedback as possible with each respin to minimize
  number of iterations.
* Focus on getting changes into a "good enough" shape and merged sooner, but
  describe desirable improvements to be required for further submissions.
* Stay on topic of the changes and improvements to minimize stray
  conversations and unnecessary argument.
