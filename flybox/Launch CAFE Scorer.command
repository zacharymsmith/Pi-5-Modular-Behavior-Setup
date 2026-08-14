#!/bin/bash
# Opens the CAFE Analysis Suite (scoring is step 1 inside it). Uses the isolated conda env.
cd "$(dirname "$0")"
exec "./Launch CAFE Suite.command"
