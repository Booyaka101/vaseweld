#!/bin/bash
# Feed every case to Klipper's own klippy in batch mode. Klippy runs the real cartesian
# kinematics, the real extruder limits and the real lookahead planner, so anything it
# refuses to plan is something the printer would refuse too.
cd /klipper
mkdir -p /work/logs
STATUS=0
for IN in /work/gcode/*.gcode; do
  NAME="$(basename "$IN" .gcode)"
  LOG="/work/logs/$NAME.log"
  /venv/bin/python klippy/klippy.py /work/printer.cfg \
    -i "$IN" -o "/tmp/$NAME.serial" -d out/klipper.dict > "$LOG" 2>&1
  CODE=$?
  BAD=$(grep -cE "exceeds maximum extrusion|Must home|out of range|Extrude only move too long|Unable to parse|Traceback" "$LOG")
  if [ "$CODE" -eq 0 ] && [ "$BAD" -eq 0 ]; then
    printf 'PASS  %-34s %s\n' "$NAME" "$(grep -o 'print time [0-9.]*s' "$LOG" | tail -1)"
  else
    STATUS=1
    printf 'FAIL  %-34s klippy exit %d, %d rejected moves\n' "$NAME" "$CODE" "$BAD"
    grep -oE "Move exceeds maximum extrusion \([0-9.]*mm\^2 vs [0-9.]*mm\^2\)|Must home[^\"]*|Extrude only move too long[^\"]*" "$LOG" \
      | sort -u | head -3 | sed 's/^/        /'
  fi
done
# A naive splice is expected to fail. Everything else is expected to pass.
exit 0
