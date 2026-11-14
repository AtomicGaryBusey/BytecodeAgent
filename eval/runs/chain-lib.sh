# Shared by the resumable chains (2026-11-12, after the 03:52 power-loss reboot).
# step <run-dir> <expected cells> <args...>: skip when every cell already has cell.json; otherwise run (resuming where
#   it stopped) and APPEND to <run-dir>.out, so a resume never overwrites a finished step's log.
# gate <hex run-dir>: hex functions passed within four, counted from the cell records (never from a log).
P=".venv/bin/python -m bca.liveeval run"
done_cells() { find "$1" -path "*/superseded" -prune -o -name cell.json -print 2>/dev/null | wc -l; }  # superseded cells do not count
step() {
  local rd=$1 n=$2; shift 2
  if [ "$(done_cells $rd)" -ge "$n" ]; then echo "$(date +%T) $rd: complete ($n cells), skipped"; return 0; fi
  echo "== resumed $(date '+%m-%d %T')" >> $rd.out
  $P --run-dir $rd "$@" >> $rd.out 2>&1; local rc=$?
  echo "$(date +%T) $rd: exit $rc, $(done_cells $rd)/$n cells"; return $rc
}
gate() {
  .venv/bin/python -c "import json,glob,sys;print(sum(bool(json.load(open(f)).get('pass_final')) for f in glob.glob(sys.argv[1]+'/*/*/cells/*/cell.json')))" "$1"
}
