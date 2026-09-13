param(
    [Parameter(Position=0)]
    [string]$Command = "daemon"
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$scriptDir\talk.py" $Command
