#!/usr/bin/env python3

import os
import re
import csv
import sys
import argparse

def cache_size_to_bytes(cache_str):
    if not cache_str or cache_str == "N/A":
        return 0
    m = re.match(r'(\d+)\s*([A-Za-z]+)?', str(cache_str))
    if not m:
        return 0
    val = int(m.group(1))
    unit = (m.group(2) or '').upper()
    if 'M' in unit:
        return val * 1024 * 1024
    elif 'G' in unit:
        return val * 1024 * 1024 * 1024
    elif 'K' in unit:
        return val * 1024
    return val

def parse_log_file(file_path):
    def parse_float(val):
        if val is None or val == "N/A":
            return 0.0
        s = str(val).replace('%', '').replace('ms', '').strip()
        try:
            return float(s)
        except ValueError:
            return 0.0
    metrics = {}
    
    # Extract info from filename
    filename = os.path.basename(file_path)
    metrics['Log File'] = filename
    # Example filename formats:
    # log_ssd_config_general_cache_128KiB_msc_hm_0.csv_CCFTL
    # log_ssd_config_cache_128KiB_trace_benchmark_long_gc_stress_seed2026_CCFTL
    match = re.search(r'log_(.*)_cache_([^_]+)_(.*)_([^_]+)$', filename)
    if match:
        metrics['SSD Config'] = match.group(1)
        metrics['Cache Size'] = match.group(2)
        metrics['Workload']   = match.group(3)
        metrics['FTL Scheme'] = match.group(4)
    else:
        match_trace = re.search(r'log_(.*)_trace_(.*)_seed\d+_(.*)$', filename)
        if match_trace:
            metrics['SSD Config'] = match_trace.group(1)
            metrics['Cache Size'] = "N/A"
            metrics['Workload']   = match_trace.group(2)
            metrics['FTL Scheme'] = match_trace.group(3)
        else:
            metrics['SSD Config'] = "N/A"
            metrics['Cache Size'] = "N/A"
            metrics['Workload']   = "N/A"
            metrics['FTL Scheme'] = "N/A"

    try:
        with open(file_path, 'r', errors='ignore') as f:
            content = f.read()
            
            # Extract metrics using regex
            def get_val(pattern, text, group=1):
                m = re.search(pattern, text)
                return m.group(group).strip() if m else "N/A"

            metrics['Host Read Requests']  = get_val(r'Total Host Read Requests\s*:\s*(\d+)', content)
            metrics['Host Read Pages']     = get_val(r'Total Host Read Pages\s*:\s*(\d+)', content)
            metrics['Host Write Requests'] = get_val(r'Total Host Write Requests\s*:\s*(\d+)', content)
            metrics['Host Write Pages']    = get_val(r'Total Host Write Pages\s*:\s*(\d+)', content)
            metrics['Flash Read Pages']    = get_val(r'Total Flash Read Pages\s*:\s*(\d+)', content)
            metrics['Flash Write Pages']   = get_val(r'Total Flash Write Pages\s*:\s*(\d+)', content)
            metrics['Flash Erase Blocks']  = get_val(r'Total Flash Erase Operations\s*:\s*(\d+)', content)
            metrics['WAF']                 = get_val(r'WAF\s*=\s*([\d.]+)', content)
            metrics['RAF']                 = get_val(r'RAF\s*=\s*([\d.]+)', content)
            metrics['GC Runs']             = get_val(r'GC Runs\s*:\s*(\d+)', content)
            metrics['GTE Entries']         = get_val(r'Translation Pages Used \(GTD Entries\)\s*:\s*(\d+)', content)
            metrics['CMT Hit Rate']        = get_val(r'CMT Hit Rate by Page\s*:\s*([\d.%]+)', content)
            metrics['Latency']             = get_val(r'Latency\s*:\s*([\d]+)', content)
            metrics['Average Response Time'] = parse_float(metrics['Latency']) / (parse_float(metrics['Host Read Requests']) + parse_float(metrics['Host Write Requests']))
            metrics['Total Mappings']      = get_val(r'Total Mapings\s*:\s*(\d+)', content)
            metrics['Mapping Coverage']    = parse_float(metrics['Total Mappings']) / parse_float(metrics['GTE Entries'])
            
    except Exception as e:
        # Silently fail for individual files to avoid cluttering output
        pass

    return metrics

PLOT_METRICS = [
    'Flash Erase Blocks',
    'WAF',
    'RAF',
    'GC Runs',
    'GTE Entries',
    'CMT Hit Rate',
    'Latency',
    'Average Response Time',
    'Mapping Coverage'
]

FTL_SCHEME_ORDER = {
    'DFTL': 0,
    'CDFTL': 1,
    'CCFTL': 2,
    'rFTL': 3,
}


def ftl_scheme_sort_key(scheme):
    return FTL_SCHEME_ORDER.get(str(scheme).strip(), len(FTL_SCHEME_ORDER))


def generate_plots(all_results, output_dir='plots'):
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("matplotlib and numpy are required for plotting. Please install them with 'pip install matplotlib numpy'.")
        return

    os.makedirs(output_dir, exist_ok=True)
    
    def parse_float(val):
        if val is None or val == "N/A":
            return 0.0
        s = str(val).replace('%', '').replace('ms', '').strip()
        try:
            return float(s)
        except ValueError:
            return 0.0

    cache_sizes = sorted(list(set(r.get('Cache Size', 'N/A') for r in all_results)), key=cache_size_to_bytes)
    
    for c_size in cache_sizes:
        c_results = [r for r in all_results if r.get('Cache Size') == c_size]
        if not c_results:
            continue
        
        if len(cache_sizes) > 1:
            c_dir = os.path.join(output_dir, f"cache_{c_size}")
        else:
            c_dir = output_dir
        os.makedirs(c_dir, exist_ok=True)
        
        workloads = sorted(list(set(r.get('Workload', '') for r in c_results)))
        schemes = sorted(list(set(r.get('FTL Scheme', '') for r in c_results)), key=ftl_scheme_sort_key)
        
        if not workloads or not schemes:
            continue

        for metric in PLOT_METRICS:
            plot_schemes = schemes
            scheme_labels = schemes
            if metric in ('GTE Entries', 'Mapping Coverage'):
                plot_schemes = [scheme for scheme in schemes if scheme in ('CCFTL', 'rFTL')]
                scheme_labels = ['TRAD' if scheme == 'CCFTL' else scheme for scheme in plot_schemes]

            if not plot_schemes:
                continue

            data = np.zeros((len(plot_schemes), len(workloads)))
            baseline_values = {}
            if metric == 'Average Response Time':
                for workload in workloads:
                    baseline_matching = [
                        r for r in c_results
                        if r.get('FTL Scheme') == 'rFTL' and r.get('Workload') == workload
                    ]
                    if baseline_matching:
                        baseline_values[workload] = parse_float(
                            baseline_matching[0].get(metric, 0)
                        )

            for s_idx, scheme in enumerate(plot_schemes):
                for w_idx, workload in enumerate(workloads):
                    matching = [r for r in c_results if r.get('FTL Scheme') == scheme and r.get('Workload') == workload]
                    if matching:
                        value = parse_float(matching[0].get(metric, 0))
                        if metric == 'Average Response Time':
                            baseline = baseline_values.get(workload, 0.0)
                            value = value / baseline if baseline else 0.0
                        data[s_idx, w_idx] = value
            
            x = np.arange(len(workloads))
            width = 0.8 / len(plot_schemes)
            
            fig, ax = plt.subplots(figsize=(max(10, len(workloads) * 0.8), 6))
            
            colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']
            for s_idx, scheme_label in enumerate(scheme_labels):
                offset = (s_idx - (len(plot_schemes) - 1) / 2) * width
                ax.bar(x + offset, data[s_idx], width, label=scheme_label, color=colors[s_idx % len(colors)])
            
            clean_workloads = [w.replace('.csv', '').replace('.trace', '') for w in workloads]
            
            #ax.set_xlabel('Traces', fontsize=12, fontweight='bold')
            ylabel = metric
            if metric == 'Average Response Time':
                ylabel = 'Normalized average response time'
            if metric == 'GTE Entries':
                ylabel = 'Number of physical translation pages'
            if metric == 'Mapping Coverage':
                ylabel = 'Average number of mappings'
            if metric == "CMT Hit Rate":
                ylabel = 'CMT hit rate'
            if metric == 'Flash Erase Blocks':
                ylabel = 'Flash erase blocks'
            ax.set_ylabel(ylabel, fontsize=12, fontweight='bold')
            ax.set_xlabel("Workloads", fontsize=12, fontweight='bold')
            #ax.set_title(f'{metric} Comparison (Cache Size: {c_size})', fontsize=14, fontweight='bold', pad=50)
            if metric == "CMT Hit Rate":
                c_size_clear = "".join(c_size.split('i'))
                ax.set_title(f'{metric} Comparison (Cache Size: {c_size_clear})', fontsize=14, fontweight='bold', pad=50)
            elif metric == "GTE Entries":
                ax.set_title(f'Number of Physical Translation Pages Comparison', fontsize=14, fontweight='bold', pad=50)
            else:
                ax.set_title(f'{metric} Comparison', fontsize=14, fontweight='bold', pad=50)
            ax.set_xticks(x)
            ax.set_xticklabels(clean_workloads, rotation=45, ha='right', fontsize=9)
            ax.legend(
                #title='FTL Scheme',
                loc='lower center',
                bbox_to_anchor=(0.5, 1.02),
                ncol=min(5, len(plot_schemes)),
                fontsize=10,
                frameon=True
            )
            ax.grid(axis='y', linestyle='--', alpha=0.5)
            
            fig.tight_layout()
            
            safe_metric = metric.lower().replace(' ', '_')
            chart_path = os.path.join(c_dir, f"{safe_metric}_{c_size}.eps")
            fig.savefig(chart_path, format="eps", dpi=300)
            plt.close(fig)
            print(f"Chart saved: {chart_path}")

def main():
    parser = argparse.ArgumentParser(description='Summarize FTL simulator logs.')
    parser.add_argument('log_dir', nargs='?', help='Directory containing log files')
    parser.add_argument('--csv', help='Export to CSV file')
    parser.add_argument('--plot', nargs='?', const='plots', help='Generate bar charts comparing schemes (optional output dir, default: plots)')
    args = parser.parse_args()

    log_dir = args.log_dir
    if not log_dir or not os.path.exists(log_dir):
        print(f"Directory not found or missing: {log_dir}")
        return

    files = [f for f in os.listdir(log_dir) if f.startswith('log_')]

    all_results = []
    for filename in files:
        result = parse_log_file(os.path.join(log_dir, filename))
        if result:
            all_results.append(result)

    if not all_results:
        print("No results found.")
        return

    # Sort results logically: Workload -> Cache Size -> FTL Scheme -> SSD Config
    all_results.sort(key=lambda x: (
        x.get('Workload', ''),
        cache_size_to_bytes(x.get('Cache Size', '')),
        ftl_scheme_sort_key(x.get('FTL Scheme', '')),
        x.get('SSD Config', ''),
        x.get('Log File', '')
    ))

    # Define columns
    columns = [
        'Log File', 'SSD Config', 'Cache Size', 'Workload', 'FTL Scheme', 
        'Host Read Requests', 'Host Read Pages', 'Host Write Requests', 'Host Write Pages',
        'Flash Read Pages', 'Flash Write Pages', 'Flash Erase Blocks',
        'WAF', 'RAF', 'GC Runs', 'GTE Entries', 'CMT Hit Rate',
        'Latency', 'Average Response Time', 'Mapping Coverage'
    ]

    if args.csv:
        with open(args.csv, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=columns)
            writer.writeheader()
            for res in all_results:
                writer.writerow({k: res.get(k, "N/A") for k in columns})
        print(f"Results exported to {args.csv}")
    else:
        # Print as Markdown Table
        header = "| " + " | ".join(columns) + " |"
        separator = "| " + " | ".join(["---"] * len(columns)) + " |"
        print(header)
        print(separator)
        for res in all_results:
            row = "| " + " | ".join([str(res.get(col, "N/A")) for col in columns]) + " |"
            print(row)

    if args.plot:
        plot_dir = args.plot
        print(f"Generating charts in '{plot_dir}'...")
        generate_plots(all_results, plot_dir)

if __name__ == "__main__":
    main()

