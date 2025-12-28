// Phase Error Scatter and Histogram panels
// Shared query ensures Grafana can cache/reuse results

// Shared sample query - used by both scatter and histogram
local sampleQuery = |||
  from(bucket: "fts")
    |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
    |> filter(fn: (r) => r["_measurement"] == "edges")
    |> filter(fn: (r) => r["_field"] == "delay_ns")
    |> sample(n: 2000)
|||;

// Mean overlay query for scatter plot (uses full dataset)
local meanQuery = |||
  from(bucket: "fts")
    |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
    |> filter(fn: (r) => r["_measurement"] == "edges")
    |> filter(fn: (r) => r["_field"] == "delay_ns")
    |> aggregateWindow(every: 60s, fn: mean, createEmpty: false)
    |> set(key: "_field", value: "mean_60s")
|||;

local datasource = { type: 'influxdb', uid: 'influxdb' };

// Histogram panel
local histogramPanel = {
  datasource: datasource,
  fieldConfig: {
    defaults: {
      color: { mode: 'palette-classic' },
      custom: {
        fillOpacity: 80,
        gradientMode: 'none',
        hideFrom: { legend: false, tooltip: false, viz: false },
        lineWidth: 1,
      },
      mappings: [],
      thresholds: {
        mode: 'absolute',
        steps: [{ color: 'green', value: null }],
      },
      unit: 'ns',
    },
    overrides: [],
  },
  gridPos: { h: 13, w: 8, x: 12, y: 8 },
  id: 6,
  options: {
    bucketCount: 30,
    legend: {
      calcs: ['min', 'mean', 'max', 'stdDev'],
      displayMode: 'table',
      placement: 'bottom',
      showLegend: true,
    },
    tooltip: { mode: 'multi', sort: 'none' },
  },
  title: 'Phase Error Histogram (edges, sampled 2000)',
  type: 'histogram',
  targets: [
    {
      datasource: datasource,
      query: sampleQuery,
      refId: 'A',
    },
  ],
};

// Scatter panel
local scatterPanel = {
  datasource: datasource,
  fieldConfig: {
    defaults: {
      color: { mode: 'fixed', fixedColor: 'blue' },
      custom: {
        axisBorderShow: false,
        axisCenteredZero: false,
        axisColorMode: 'text',
        axisLabel: '',
        axisPlacement: 'auto',
        barAlignment: 0,
        drawStyle: 'points',
        fillOpacity: 0,
        gradientMode: 'none',
        hideFrom: { legend: false, tooltip: false, viz: false },
        insertNulls: 90000,
        lineInterpolation: 'linear',
        lineWidth: 1,
        pointSize: 2,
        scaleDistribution: { type: 'linear' },
        showPoints: 'always',
        spanNulls: false,
        stacking: { group: 'A', mode: 'none' },
        thresholdsStyle: { mode: 'off' },
      },
      mappings: [],
      thresholds: {
        mode: 'absolute',
        steps: [{ color: 'green', value: null }],
      },
      unit: 'ns',
    },
    overrides: [
      {
        matcher: { id: 'byRegexp', options: '.*mean.*' },
        properties: [
          { id: 'custom.drawStyle', value: 'line' },
          { id: 'custom.lineWidth', value: 2 },
          { id: 'custom.pointSize', value: 1 },
          { id: 'custom.showPoints', value: 'never' },
          { id: 'color', value: { fixedColor: 'red', mode: 'fixed' } },
        ],
      },
    ],
  },
  gridPos: { h: 13, w: 12, x: 0, y: 8 },
  id: 7,
  options: {
    legend: { calcs: [], displayMode: 'list', placement: 'bottom', showLegend: true },
    tooltip: { mode: 'multi', sort: 'none' },
  },
  title: 'Phase Error Scatter (edges, sampled 2000)',
  type: 'timeseries',
  targets: [
    {
      datasource: datasource,
      query: sampleQuery,
      refId: 'A',
    },
    {
      datasource: datasource,
      query: meanQuery,
      refId: 'B',
    },
  ],
};

// Output both panels
{
  scatter: scatterPanel,
  histogram: histogramPanel,
}
