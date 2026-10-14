#!/bin/bash
# Back to the admin console from before /api/rules.
set -euo pipefail
ssh fleet-console 'docker rm -f reclaimer-admin && docker rename reclaimer-admin-before-rules-20261014 reclaimer-admin && docker start reclaimer-admin && docker inspect -f "{{.Config.Image}}" reclaimer-admin'
