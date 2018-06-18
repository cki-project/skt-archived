# Changelog for skt

## June 2018

* Arguments for reporter are now explicitly defined and JSON strings are no
  longer required. Users can specify the following arguments:

  * `--reporter`: type of reporter to use (`stdio` and `mail` supported)
    *(required)*
  * `--mail-from`: email address of the sender *(required)*
  * `--mail-to`: email address of the recipient *(required)*
  * `--mail-subject`: subject line of the email *(optional)*
  * `--mail-header`: additional header to add to the email *(optional)*

  The `--mail-to` and `--mail-header` arguments can be specified more than once to select multiple recipients or add multiple headers.
