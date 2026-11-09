# The Save-time shortcut warning — Intent

## The owner's report

"why everytime I click save, it pop up this? is it necessary? Please help
address and potentially fix this" — with a screenshot of the warning "You can
save it anyway, but the other action may also run when you press it. • Turn
Live Check on/off uses Cmd+Shift+L, which is already Finder → Downloads."

## Goal

Save in Settings is silent unless the user is actually *setting* a shortcut
that something else already owns. A key the owner has been using for weeks is
not news, and saving an unrelated setting must not re-litigate it.

## Success evidence

- Saving an unchanged form performs no conflict scan and shows no dialog.
- Changing a shortcut to a taken combination still warns, once, with the same
  message.
- The duplicate/system/menu checks still work for the shortcuts that changed.
- `./scripts/run_tests.sh` passes and the beta is installed.

## Stop condition

- `done`: the owner can press Save without the dialog, and the warning still
  appears when it is genuinely useful.

## Non-goals

- Changing the default shortcuts (the owner's `Cmd+Shift+L` stays; it only
  matters if they want to change it).
- Removing the warning altogether: it exists for the moment a shortcut is
  assigned.
