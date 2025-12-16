#!/bin/bash
#
# Run all analysis combinations: files × modes × algorithms
# Generates individual reports and a comparison HTML
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="${PROJECT_DIR}/data"

# Test files to analyze
FILES=(
    "test1-mimo"
    "test2-no-mimo"
    "test3-fts"
    "test4-p250-j250"
    "test5-p-18-j15"
    "test6-p25-j15"
    "test7-p-50-j100"
)

# Modes and algorithms
MODES=("batch" "stream")
#ALGORITHMS=("crossing" "linreg")
ALGORITHMS=("crossing")

# Timestamp for comparison report
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
COMPARISON_FILE="${DATA_DIR}/comparison-${TIMESTAMP}.html"

echo "=== FTS-QA Analysis Suite ==="
echo "Data directory: ${DATA_DIR}"
echo "Comparison output: ${COMPARISON_FILE}"
echo ""

# Collect results for comparison
declare -A RESULTS

# Run all combinations
for file in "${FILES[@]}"; do
    CFILE="${DATA_DIR}/${file}.cfile"

    if [ ! -f "$CFILE" ]; then
        echo "WARNING: $CFILE not found, skipping"
        continue
    fi

    for mode in "${MODES[@]}"; do
        for algo in "${ALGORITHMS[@]}"; do
            OUTPUT_DIR="${DATA_DIR}/${file}-${mode}-${algo}"
            KEY="${file}|${mode}|${algo}"

            echo "--- Analyzing: ${file} [${mode}/${algo}] ---"

            if [ "$mode" = "batch" ]; then
                python3 "${PROJECT_DIR}/src/cli.py" analyze \
                    -o "$OUTPUT_DIR" \
                    --algorithm "$algo" \
                    "$CFILE" 2>&1 | tail -10
            else
                python3 "${PROJECT_DIR}/src/cli.py" analyze-edges \
                    -o "$OUTPUT_DIR" \
                    --algorithm "$algo" \
                    "$CFILE" 2>&1 | tail -10
            fi

            # Extract stats from output
            REPORT="${OUTPUT_DIR}/report.html"
            if [ -f "$REPORT" ]; then
                RESULTS[$KEY]="$REPORT"
            fi

            echo ""
        done
    done
done

echo "=== Generating Comparison Report ==="

# Generate comparison HTML
cat > "$COMPARISON_FILE" << 'HEADER'
<!DOCTYPE html>
<html>
<head>
    <title>FTS-QA Analysis Comparison</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 20px; }
        h1 { color: #333; }
        table { border-collapse: collapse; width: 100%; margin-top: 20px; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #4a90d9; color: white; }
        tr:nth-child(even) { background-color: #f2f2f2; }
        tr:hover { background-color: #ddd; }
        a { color: #4a90d9; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .timestamp { color: #666; font-size: 0.9em; }
        .file-header { background-color: #e8e8e8; font-weight: bold; }
    </style>
</head>
<body>
    <h1>FTS-QA Analysis Comparison</h1>
HEADER

echo "    <p class=\"timestamp\">Generated: $(date)</p>" >> "$COMPARISON_FILE"

# Build comparison table
cat >> "$COMPARISON_FILE" << 'TABLE_HEADER'
    <table>
        <tr>
            <th>File</th>
            <th>Mode</th>
            <th>Algorithm</th>
            <th>Mean (ns)</th>
            <th>Std (ns)</th>
            <th>Min (ns)</th>
            <th>Max (ns)</th>
            <th>Report</th>
        </tr>
TABLE_HEADER

for file in "${FILES[@]}"; do
    for mode in "${MODES[@]}"; do
        for algo in "${ALGORITHMS[@]}"; do
            OUTPUT_DIR="${DATA_DIR}/${file}-${mode}-${algo}"
            REPORT="${OUTPUT_DIR}/report.html"
            KEY="${file}|${mode}|${algo}"

            if [ -f "$REPORT" ]; then
                # Extract stats from HTML
                # Format: <span class="stat-label">Mean</span><span class="stat-value">+1.585 ns</span>
                MEAN=$(grep -oP 'stat-label">Mean</span><span class="stat-value">\K[^<]+' "$REPORT" 2>/dev/null | head -1 || echo "N/A")
                STD=$(grep -oP 'stat-label">Std Dev</span><span class="stat-value">\K[^<]+' "$REPORT" 2>/dev/null | head -1 || echo "N/A")
                MIN=$(grep -oP 'stat-label">Min</span><span class="stat-value">\K[^<]+' "$REPORT" 2>/dev/null | head -1 || echo "N/A")
                MAX=$(grep -oP 'stat-label">Max</span><span class="stat-value">\K[^<]+' "$REPORT" 2>/dev/null | head -1 || echo "N/A")

                # Get relative path for link
                REL_REPORT="${file}-${mode}-${algo}/report.html"

                cat >> "$COMPARISON_FILE" << ROW
        <tr>
            <td>${file}</td>
            <td>${mode}</td>
            <td>${algo}</td>
            <td>${MEAN}</td>
            <td>${STD}</td>
            <td>${MIN}</td>
            <td>${MAX}</td>
            <td><a href="${REL_REPORT}">View Report</a></td>
        </tr>
ROW
            else
                cat >> "$COMPARISON_FILE" << ROW
        <tr>
            <td>${file}</td>
            <td>${mode}</td>
            <td>${algo}</td>
            <td colspan="4">Report not found</td>
            <td>-</td>
        </tr>
ROW
            fi
        done
    done
done

cat >> "$COMPARISON_FILE" << 'FOOTER'
    </table>

    <h2>Notes</h2>
    <ul>
        <li><strong>batch</strong>: Loads entire file into memory, uses <code>analyze</code> command</li>
        <li><strong>stream</strong>: Memory-bounded streaming, uses <code>analyze-edges</code> command</li>
        <li><strong>crossing</strong>: Simple threshold crossing with linear interpolation</li>
        <li><strong>linreg</strong>: Linear regression on 20-80% edge slope</li>
    </ul>
    <p>Same algorithm should produce identical results regardless of mode (batch vs stream).</p>
</body>
</html>
FOOTER

echo ""
echo "=== Done ==="
echo "Comparison report: ${COMPARISON_FILE}"
echo "Open in browser: file://${COMPARISON_FILE}"
