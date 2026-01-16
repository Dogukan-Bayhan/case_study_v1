// Slice & Dice Studio client-side logic.
(() => {
  const metricInputs = Array.from(document.querySelectorAll("[data-metric]"));
  if (!metricInputs.length) {
    return;
  }

  const dimensionInputs = Array.from(document.querySelectorAll("[data-dimension]"));
  const scopeButtons = Array.from(document.querySelectorAll("[data-scope]"));
  const grainButtons = Array.from(document.querySelectorAll("[data-grain]"));
  const metricCount = document.getElementById("metric-count");
  const dimensionCount = document.getElementById("dimension-count");
  const dimensionWarning = document.getElementById("dimension-warning");
  const runButton = document.getElementById("run-analysis");
  const analysisStatus = document.getElementById("analysis-status");
  const resultsSubtitle = document.getElementById("results-subtitle");
  const tableContainer = document.getElementById("results-table");
  const tableStatus = document.getElementById("table-status");
  const pageInfo = document.getElementById("page-info");
  const prevButton = document.getElementById("page-prev");
  const nextButton = document.getElementById("page-next");
  const exportButton = document.getElementById("export-csv");
  const resultLimit = document.getElementById("result-limit");
  const chartContainer = document.getElementById("chart-container");
  const chartWarning = document.getElementById("chart-warning");
  const chartHint = document.getElementById("chart-hint");
  const filtersPanel = document.getElementById("builder-filters");
  const activeFiltersEl = document.getElementById("active-filters");

  const filterLabels = {
    country: "Country",
    city: "City",
    category: "Category",
    department: "Department",
    payment_method: "Payment Method",
    tier: "Tier",
    is_returning_customer: "Returning",
    rating_min: "Rating Min",
    rating_max: "Rating Max",
    quantity_min: "Quantity Min",
    quantity_max: "Quantity Max",
    unit_price_min: "Unit Price Min",
    unit_price_max: "Unit Price Max",
    discount_percent_min: "Discount Min",
    discount_percent_max: "Discount Max",
    tax_rate_min: "Tax Rate Min",
    tax_rate_max: "Tax Rate Max",
    total_amount_min: "Total Min",
    total_amount_max: "Total Max",
    order_date_start: "Order Date From",
    order_date_end: "Order Date To",
  };

  const state = {
    scope: "clean",
    metrics: new Set(),
    dimensions: new Set(),
    filters: {},
    dateGrain: "day",
    limit: Number(resultLimit.value),
    offset: 0,
    sortBy: null,
    sortDir: "desc",
    columns: [],
    rows: [],
    hasMore: false,
  };

  const decimalFormatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
  const currencyFormatter = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" });

  function formatPercent(value) {
    const numeric = Number(value || 0);
    const adjusted = numeric <= 1 ? numeric * 100 : numeric;
    return `${adjusted.toFixed(1)}%`;
  }

  function formatValue(value, format) {
    if (value === null || value === undefined) return "--";
    if (format === "currency") return currencyFormatter.format(Number(value || 0));
    if (format === "percent") return formatPercent(value);
    if (format === "number") return decimalFormatter.format(Number(value || 0));
    if (format === "date") {
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return value;
      return parsed.toLocaleDateString();
    }
    return String(value);
  }

  function updateSelectionCounts() {
    metricCount.textContent = `${state.metrics.size} selected`;
    dimensionCount.textContent = `${state.dimensions.size} / 3`;
    runButton.disabled = state.metrics.size === 0 || state.dimensions.size === 0;
    analysisStatus.textContent = runButton.disabled
      ? "Select at least one metric and one dimension."
      : "Ready to run.";
  }

  function toggleSelectedClass(input) {
    const label = input.closest(".check-item");
    if (!label) return;
    label.classList.toggle("is-selected", input.checked);
  }

  function handleMetricToggle(event) {
    const input = event.target;
    if (input.checked) {
      state.metrics.add(input.value);
    } else {
      state.metrics.delete(input.value);
    }
    toggleSelectedClass(input);
    updateSelectionCounts();
  }

  function handleDimensionToggle(event) {
    const input = event.target;
    if (input.checked && state.dimensions.size >= 3) {
      input.checked = false;
      dimensionWarning.classList.remove("hidden");
      return;
    }
    dimensionWarning.classList.add("hidden");
    if (input.checked) {
      state.dimensions.add(input.value);
    } else {
      state.dimensions.delete(input.value);
    }
    toggleSelectedClass(input);
    updateSelectionCounts();
    updateDateGrainAvailability();
  }

  function updateDateGrainAvailability() {
    const enabled = state.dimensions.has("order_date");
    document.getElementById("date-grain-block").classList.toggle("is-disabled", !enabled);
    grainButtons.forEach((button) => {
      button.disabled = !enabled;
    });
  }

  function setScope(scope) {
    state.scope = scope;
    scopeButtons.forEach((button) => {
      button.classList.toggle("is-active", button.dataset.scope === scope);
    });
  }

  function setDateGrain(grain) {
    state.dateGrain = grain;
    grainButtons.forEach((button) => {
      button.classList.toggle("is-active", button.dataset.grain === grain);
    });
  }

  function collectFilters() {
    const filters = {};
    const inputs = Array.from(filtersPanel.querySelectorAll("[data-field]"));
    inputs.forEach((input) => {
      const field = input.dataset.field;
      const value = input.value?.trim?.() ?? input.value;
      if (value !== "" && value !== null && value !== undefined) {
        filters[field] = value;
      }
    });
    return filters;
  }

  function renderActiveFilters() {
    activeFiltersEl.innerHTML = "";
    const entries = Object.entries(state.filters);
    if (!entries.length) {
      activeFiltersEl.innerHTML = '<span class="text-xs text-slate-500">No active filters.</span>';
      return;
    }
    entries.forEach(([key, value]) => {
      const chip = document.createElement("button");
      chip.className = "filter-chip";
      const label = filterLabels[key] || key;
      const displayValue = key === "is_returning_customer"
        ? value === "1" || value === 1 || value === true
          ? "Returning"
          : "New"
        : value;
      chip.innerHTML = `<span>${label}: ${displayValue}</span><span class="chip-close">x</span>`;
      chip.addEventListener("click", () => {
        const input = filtersPanel.querySelector(`[data-field="${key}"]`);
        if (input) {
          input.value = "";
        }
        delete state.filters[key];
        renderActiveFilters();
        state.offset = 0;
        if (!runButton.disabled) {
          runAnalysis();
        }
      });
      activeFiltersEl.appendChild(chip);
    });
  }

  function buildFilterParams(filters) {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value === "" || value === null || value === undefined) return;
      params.set(`filter_${key}`, value);
    });
    return params;
  }

  function buildAdHocFilters(filters) {
    const payload = {};
    const stringFields = ["country", "city", "category", "department", "payment_method", "tier"];
    stringFields.forEach((field) => {
      if (filters[field]) {
        payload[field] = filters[field];
      }
    });

    if (filters.is_returning_customer !== undefined) {
      if (filters.is_returning_customer === "1" || filters.is_returning_customer === 1 || filters.is_returning_customer === true) {
        payload.is_returning_customer = true;
      } else if (filters.is_returning_customer === "0" || filters.is_returning_customer === 0 || filters.is_returning_customer === false) {
        payload.is_returning_customer = false;
      }
    }

    const numericFields = ["rating", "quantity", "unit_price", "discount_percent", "tax_rate", "total_amount"];
    numericFields.forEach((field) => {
      const minKey = `${field}_min`;
      const maxKey = `${field}_max`;
      const minValue = filters[minKey];
      const maxValue = filters[maxKey];
      if (minValue !== undefined || maxValue !== undefined) {
        payload[field] = {};
        if (minValue !== undefined) payload[field].gte = Number(minValue);
        if (maxValue !== undefined) payload[field].lte = Number(maxValue);
      }
    });

    const start = filters.order_date_start;
    const end = filters.order_date_end;
    if (start || end) {
      payload.order_date = {};
      if (start) payload.order_date.from = start;
      if (end) payload.order_date.to = end;
    }

    return payload;
  }

  function setLoadingState() {
    tableContainer.innerHTML = `
      <div class="skeleton-table">
        <div class="skeleton skeleton-block"></div>
        <div class="skeleton skeleton-block"></div>
        <div class="skeleton skeleton-block"></div>
        <div class="skeleton skeleton-block"></div>
      </div>
    `;
    chartContainer.innerHTML = '<div class="skeleton skeleton-fill"></div>';
    tableStatus.textContent = "Loading...";
    chartHint.textContent = "Loading chart...";
  }

  function updatePagination() {
    const page = Math.floor(state.offset / state.limit) + 1;
    pageInfo.textContent = `Page ${page}`;
    prevButton.disabled = state.offset === 0;
    nextButton.disabled = !state.hasMore;
  }

  function renderTable(columns, rows) {
    if (!columns.length) {
      tableContainer.innerHTML = '<div class="empty-state">No columns returned.</div>';
      return;
    }
    if (!rows.length) {
      tableContainer.innerHTML = '<div class="empty-state">No rows matched this selection.</div>';
      return;
    }

    const header = columns.map((column) => {
      const isSorted = state.sortBy === column.key;
      const arrow = isSorted ? (state.sortDir === "asc" ? "asc" : "desc") : "";
      const sortableClass = "table-sort";
      return `<th data-key="${column.key}" class="${sortableClass}">${column.label} ${arrow}</th>`;
    }).join("");

    const body = rows.map((row) => {
      const cells = columns.map((column) => `<td>${formatValue(row[column.key], column.format)}</td>`).join("");
      return `<tr>${cells}</tr>`;
    }).join("");

    tableContainer.innerHTML = `
      <div class="table-scroll">
        <table class="results-table">
          <thead>
            <tr>${header}</tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    `;

    tableContainer.querySelectorAll("th[data-key]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.key;
        if (state.sortBy === key) {
          state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
        } else {
          state.sortBy = key;
          state.sortDir = "desc";
        }
        state.offset = 0;
        runAnalysis();
      });
    });
  }

  function sanitizeLabel(value) {
    if (value === null || value === undefined) return "Unknown";
    return String(value);
  }

  function truncateLabel(label, max = 12) {
    if (label.length <= max) return label;
    return `${label.slice(0, max)}...`;
  }

  function renderEmptyChart(message) {
    chartContainer.innerHTML = `<div class="empty-state">${message}</div>`;
  }

  function renderBarChart(rows, dimensionKey, metricKey, metricLabel) {
    const width = 720;
    const height = 280;
    const padding = { top: 20, right: 24, bottom: 50, left: 52 };
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;
    const values = rows.map((row) => Number(row[metricKey] || 0));
    const maxValue = Math.max(...values, 1);
    const gap = chartWidth / rows.length;
    const barWidth = Math.max(8, gap * 0.6);

    const ticks = 4;
    let svg = `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">`;
    for (let i = 0; i <= ticks; i += 1) {
      const y = padding.top + chartHeight - (chartHeight * i) / ticks;
      const label = formatValue((maxValue * i) / ticks, "number");
      svg += `<line class="chart-grid" x1="${padding.left}" x2="${width - padding.right}" y1="${y}" y2="${y}" />`;
      svg += `<text class="chart-label" x="${padding.left - 8}" y="${y + 4}" text-anchor="end">${label}</text>`;
    }

    rows.forEach((row, index) => {
      const value = Number(row[metricKey] || 0);
      const barHeight = (value / maxValue) * chartHeight;
      const x = padding.left + index * gap + (gap - barWidth) / 2;
      const y = padding.top + chartHeight - barHeight;
      const label = sanitizeLabel(row[dimensionKey]);
      svg += `
        <rect class="chart-bar" x="${x}" y="${y}" width="${barWidth}" height="${barHeight}" rx="4" fill="#fbbf24" fill-opacity="0.75">
          <title>${label}: ${formatValue(value, "number")} ${metricLabel}</title>
        </rect>
      `;
      if (index % Math.ceil(rows.length / 6) === 0) {
        svg += `<text class="chart-label" x="${x + barWidth / 2}" y="${height - 16}" text-anchor="middle">${truncateLabel(label)}</text>`;
      }
    });
    svg += `<line class="chart-axis" x1="${padding.left}" x2="${width - padding.right}" y1="${height - padding.bottom}" y2="${height - padding.bottom}" />`;
    svg += "</svg>";
    chartContainer.innerHTML = svg;
  }

  function renderLineChart(rows, dimensionKey, metricKey, metricLabel) {
    const width = 720;
    const height = 280;
    const padding = { top: 20, right: 24, bottom: 50, left: 52 };
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;
    const sorted = rows.slice().sort((a, b) => new Date(a[dimensionKey]) - new Date(b[dimensionKey]));
    const values = sorted.map((row) => Number(row[metricKey] || 0));
    const maxValue = Math.max(...values, 1);

    const ticks = 4;
    let svg = `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">`;
    for (let i = 0; i <= ticks; i += 1) {
      const y = padding.top + chartHeight - (chartHeight * i) / ticks;
      const label = formatValue((maxValue * i) / ticks, "number");
      svg += `<line class="chart-grid" x1="${padding.left}" x2="${width - padding.right}" y1="${y}" y2="${y}" />`;
      svg += `<text class="chart-label" x="${padding.left - 8}" y="${y + 4}" text-anchor="end">${label}</text>`;
    }

    const points = sorted.map((row, index) => {
      const x = padding.left + (index / (sorted.length - 1 || 1)) * chartWidth;
      const y = padding.top + chartHeight - ((Number(row[metricKey] || 0) / maxValue) * chartHeight);
      return `${x},${y}`;
    });
    svg += `<polyline class="chart-line" points="${points.join(" ")}" />`;

    sorted.forEach((row, index) => {
      const x = padding.left + (index / (sorted.length - 1 || 1)) * chartWidth;
      const value = Number(row[metricKey] || 0);
      const y = padding.top + chartHeight - ((value / maxValue) * chartHeight);
      const label = sanitizeLabel(row[dimensionKey]);
      svg += `
        <circle class="chart-point" cx="${x}" cy="${y}" r="3">
          <title>${label}: ${formatValue(value, "number")} ${metricLabel}</title>
        </circle>
      `;
      if (index % Math.ceil(sorted.length / 6) === 0) {
        svg += `<text class="chart-label" x="${x}" y="${height - 16}" text-anchor="middle">${truncateLabel(label, 8)}</text>`;
      }
    });
    svg += `<line class="chart-axis" x1="${padding.left}" x2="${width - padding.right}" y1="${height - padding.bottom}" y2="${height - padding.bottom}" />`;
    svg += "</svg>";
    chartContainer.innerHTML = svg;
  }

  function renderStackedBar(rows, primaryKey, secondaryKey, metricKey, metricLabel) {
    const palette = ["#fbbf24", "#38bdf8", "#f472b6", "#a3e635", "#fb7185", "#22d3ee"];
    const grouped = {};
    const stackKeys = [];
    rows.forEach((row) => {
      const primary = sanitizeLabel(row[primaryKey]);
      const secondary = sanitizeLabel(row[secondaryKey]);
      if (!grouped[primary]) grouped[primary] = {};
      if (!grouped[primary][secondary]) grouped[primary][secondary] = 0;
      grouped[primary][secondary] += Number(row[metricKey] || 0);
      if (!stackKeys.includes(secondary)) stackKeys.push(secondary);
    });

    const labels = Object.keys(grouped);
    const totals = labels.map((label) => stackKeys.reduce((sum, key) => sum + (grouped[label][key] || 0), 0));
    const maxValue = Math.max(...totals, 1);

    const width = 720;
    const height = 280;
    const padding = { top: 20, right: 24, bottom: 60, left: 52 };
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;
    const gap = chartWidth / labels.length;
    const barWidth = Math.max(10, gap * 0.6);

    let svg = `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">`;
    const ticks = 4;
    for (let i = 0; i <= ticks; i += 1) {
      const y = padding.top + chartHeight - (chartHeight * i) / ticks;
      const label = formatValue((maxValue * i) / ticks, "number");
      svg += `<line class="chart-grid" x1="${padding.left}" x2="${width - padding.right}" y1="${y}" y2="${y}" />`;
      svg += `<text class="chart-label" x="${padding.left - 8}" y="${y + 4}" text-anchor="end">${label}</text>`;
    }

    labels.forEach((label, index) => {
      const x = padding.left + index * gap + (gap - barWidth) / 2;
      let yOffset = 0;
      stackKeys.forEach((stackKey, stackIndex) => {
        const value = grouped[label][stackKey] || 0;
        const heightValue = (value / maxValue) * chartHeight;
        const y = padding.top + chartHeight - yOffset - heightValue;
        const color = palette[stackIndex % palette.length];
        svg += `
          <rect class="chart-bar" x="${x}" y="${y}" width="${barWidth}" height="${heightValue}" fill="${color}" rx="4">
            <title>${label} - ${stackKey}: ${formatValue(value, "number")} ${metricLabel}</title>
          </rect>
        `;
        yOffset += heightValue;
      });
      if (index % Math.ceil(labels.length / 6) === 0) {
        svg += `<text class="chart-label" x="${x + barWidth / 2}" y="${height - 20}" text-anchor="middle">${truncateLabel(label)}</text>`;
      }
    });

    svg += `<line class="chart-axis" x1="${padding.left}" x2="${width - padding.right}" y1="${height - padding.bottom}" y2="${height - padding.bottom}" />`;
    svg += "</svg>";

    const legend = stackKeys.map((key, index) => {
      const color = palette[index % palette.length];
      return `<span class="legend-item"><span class="legend-swatch" style="background:${color}"></span>${truncateLabel(key, 14)}</span>`;
    }).join("");

    chartContainer.innerHTML = `
      <div class="chart-legend">${legend}</div>
      ${svg}
    `;
  }

  function renderChart() {
    chartWarning.classList.add("hidden");
    const dimensions = Array.from(state.dimensions);
    if (!state.rows.length) {
      renderEmptyChart("No chart data available.");
      return;
    }
    if (dimensions.length > 2) {
      chartWarning.textContent = "Chart disabled for 3+ dimensions.";
      chartWarning.classList.remove("hidden");
      renderEmptyChart("Reduce dimensions to render a chart.");
      return;
    }
    const metricKey = state.columns.find((column) => column.role === "metric")?.key || state.columns[0]?.key;
    const metricLabel = state.columns.find((column) => column.key === metricKey)?.label || "";
    if (!metricKey) {
      renderEmptyChart("Select a metric to render the chart.");
      return;
    }
    if (dimensions.length === 1) {
      const dimensionKey = dimensions[0] === "order_date" ? "order_date" : dimensions[0];
      if (dimensionKey === "order_date") {
        chartHint.textContent = "Line chart for time-based analysis.";
        renderLineChart(state.rows, dimensionKey, metricKey, metricLabel);
      } else {
        chartHint.textContent = "Bar chart for single-dimension comparison.";
        renderBarChart(state.rows, dimensionKey, metricKey, metricLabel);
      }
      return;
    }
    if (dimensions.length === 2) {
      chartHint.textContent = "Stacked bar chart for two-dimensional breakdowns.";
      renderStackedBar(state.rows, dimensions[0], dimensions[1], metricKey, metricLabel);
    }
  }

  function buildPayload() {
    const selectedMetrics = Array.from(state.metrics);
    const selectedDimensions = Array.from(state.dimensions);
    const filters = buildAdHocFilters(state.filters);
    return {
      scope: state.scope,
      metrics: selectedMetrics,
      dimensions: selectedDimensions,
      filters: Object.keys(filters).length ? filters : null,
      limit: state.limit,
      offset: state.offset,
      sort_by: state.sortBy,
      sort_dir: state.sortDir,
      date_grain: state.dateGrain,
    };
  }

  async function runAnalysis() {
    if (state.metrics.size === 0 || state.dimensions.size === 0) return;
    setLoadingState();
    runButton.disabled = true;
    analysisStatus.textContent = "Running analysis...";
    try {
      const response = await fetch("/analytics/ad-hoc", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildPayload()),
      });
      if (!response.ok) {
        throw new Error("Request failed");
      }
      const data = await response.json();
      state.columns = data.columns || [];
      state.rows = data.rows || [];
      state.limit = data.limit || state.limit;
      state.offset = data.offset || 0;
      state.sortBy = data.sort_by;
      state.sortDir = data.sort_dir;
      state.hasMore = data.has_more;

      const filterCount = Object.keys(state.filters).length;
      resultsSubtitle.textContent = `${state.metrics.size} metrics | ${state.dimensions.size} dimensions | ${filterCount ? `${filterCount} filters` : "No filters"} | Scope ${state.scope.toUpperCase()}`;
      tableStatus.textContent = `Showing ${state.rows.length} rows`;
      analysisStatus.textContent = "Analysis complete.";

      renderTable(state.columns, state.rows);
      renderChart();
      updatePagination();
    } catch (err) {
      tableContainer.innerHTML = '<div class="empty-state">Failed to load results.</div>';
      chartContainer.innerHTML = '<div class="empty-state">Chart unavailable.</div>';
      chartHint.textContent = "Chart unavailable.";
      tableStatus.textContent = "Error";
      analysisStatus.textContent = "Failed to run analysis.";
    } finally {
      runButton.disabled = state.metrics.size === 0 || state.dimensions.size === 0;
    }
  }

  function bindInputs() {
    metricInputs.forEach((input) => input.addEventListener("change", handleMetricToggle));
    dimensionInputs.forEach((input) => input.addEventListener("change", handleDimensionToggle));
    scopeButtons.forEach((button) => {
      if (button.disabled) return;
      button.addEventListener("click", () => {
        setScope(button.dataset.scope);
        if (!runButton.disabled) {
          runAnalysis();
        }
      });
    });
    grainButtons.forEach((button) => {
      button.addEventListener("click", () => {
        if (button.disabled) return;
        setDateGrain(button.dataset.grain);
        if (state.dimensions.has("order_date") && !runButton.disabled) {
          runAnalysis();
        }
      });
    });
    document.getElementById("filters-apply").addEventListener("click", () => {
      state.filters = collectFilters();
      renderActiveFilters();
      state.offset = 0;
      if (!runButton.disabled) runAnalysis();
    });
    document.getElementById("filters-reset").addEventListener("click", () => {
      filtersPanel.querySelectorAll("[data-field]").forEach((input) => {
        input.value = "";
      });
      state.filters = {};
      renderActiveFilters();
      state.offset = 0;
      if (!runButton.disabled) runAnalysis();
    });
    runButton.addEventListener("click", () => {
      state.offset = 0;
      runAnalysis();
    });
    prevButton.addEventListener("click", () => {
      if (state.offset === 0) return;
      state.offset = Math.max(0, state.offset - state.limit);
      runAnalysis();
    });
    nextButton.addEventListener("click", () => {
      if (!state.hasMore) return;
      state.offset += state.limit;
      runAnalysis();
    });
    if (exportButton) {
      exportButton.addEventListener("click", () => {
        if (!state.columns.length || !state.rows.length) return;
        const header = state.columns.map((column) => `"${column.label.replace(/\"/g, '\"\"')}"`).join(",");
        const rows = state.rows.map((row) => {
          return state.columns.map((column) => {
            const raw = row[column.key];
            if (raw === null || raw === undefined) return "";
            const value = String(raw).replace(/\"/g, '\"\"');
            return `"${value}"`;
          }).join(",");
        }).join("\n");
        const csv = `${header}\n${rows}`;
        const blob = new Blob([csv], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `slice_dice_${new Date().toISOString().slice(0, 10)}.csv`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
      });
    }
    resultLimit.addEventListener("change", () => {
      state.limit = Number(resultLimit.value);
      state.offset = 0;
      if (!runButton.disabled) runAnalysis();
    });
  }

  function bindAutocomplete() {
    const inputs = Array.from(filtersPanel.querySelectorAll("input[data-field]"));
    inputs.forEach((input) => {
      const field = input.dataset.field;
      const datalistId = input.getAttribute("list");
      if (!datalistId) return;
      let timeout;
      const loadOptions = async () => {
        const currentFilters = collectFilters();
        delete currentFilters[field];
        const params = buildFilterParams(currentFilters);
        params.set("scope", state.scope);
        params.set("field", field);
        if (input.value) {
          params.set("q", input.value);
        }
        try {
          const response = await fetch(`/analytics/filter-options?${params.toString()}`);
          if (!response.ok) return;
          const data = await response.json();
          const list = document.getElementById(datalistId);
          list.innerHTML = "";
          data.forEach((option) => {
            const item = document.createElement("option");
            item.value = option.value;
            list.appendChild(item);
          });
        } catch (err) {
          // Ignore autocomplete failures.
        }
      };
      input.addEventListener("focus", () => loadOptions());
      input.addEventListener("input", () => {
        clearTimeout(timeout);
        timeout = setTimeout(loadOptions, 250);
      });
    });
  }

  updateSelectionCounts();
  updateDateGrainAvailability();
  setScope("clean");
  setDateGrain("day");
  renderActiveFilters();
  bindInputs();
  bindAutocomplete();
})();
