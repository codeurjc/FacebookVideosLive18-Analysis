import pandas as pd
import networkx as nx
import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objects as go

# Initialize global variables
GRAPH = nx.DiGraph()
CURRENT_STEP = 1
CSV_FILE = "simulated/link-instance-small-0.csv"
csv_data = pd.read_csv(CSV_FILE)
UNDO_STACK = []  # Stack to store graph states for undo functionality
UNDO_STEP_STACK = []

def save_graph_state():
    global UNDO_STACK, GRAPH, CURRENT_STEP
    UNDO_STACK.append(GRAPH.copy())
    UNDO_STEP_STACK.append(CURRENT_STEP)

def restore_previous_state():
    global UNDO_STACK, GRAPH, CURRENT_STEP
    if UNDO_STACK:
        GRAPH = UNDO_STACK.pop()
        CURRENT_STEP = UNDO_STEP_STACK.pop()

def update_node_data(step_row):
    server_id = step_row["server_id"]
    GRAPH.nodes[server_id]["capacity"] = step_row["remaining_server_capacity"]
    GRAPH.nodes[server_id]["usage"] = step_row["server_usage_pct"]
    GRAPH.nodes[server_id]["time_cost"] = step_row["server_time_cost"]

def update_graph(step_row):
    global GRAPH

    server_id = step_row["server_id"]
    server_event = step_row["server_event"]
    remaining_capacity = step_row["remaining_server_capacity"]
    server_usage = step_row["server_usage_pct"]
    server_time_cost = step_row["server_time_cost"]

    if server_event == 0:  # Create a new node
        if server_id not in GRAPH:
            GRAPH.add_node(server_id, 
                           capacity=remaining_capacity, 
                           usage=server_usage, 
                           time_cost=server_time_cost)
        # Add edges if applicable
        for existing_node in GRAPH.nodes:
            if existing_node != server_id and step_row["id"] in existing_node:
                GRAPH.add_edge(existing_node, server_id)

    elif server_event in [1, 3]:  # Update the node
        if server_id in GRAPH:
            update_node_data(step_row)

    elif server_event == 2:  # Delete the node
        if server_id in GRAPH:
            nodes_to_remove = [n for n in nx.descendants(GRAPH, server_id)] + [server_id]
            GRAPH.remove_nodes_from(nodes_to_remove)

def create_graph_figure():
    pos = nx.spring_layout(GRAPH)
    edge_x = []
    edge_y = []

    for edge in GRAPH.edges():
        x0, y0 = pos[edge[0]]
        x1, y1 = pos[edge[1]]
        edge_x.append(x0)
        edge_x.append(x1)
        edge_x.append(None)
        edge_y.append(y0)
        edge_y.append(y1)
        edge_y.append(None)

    edge_trace = go.Scatter(
        x=edge_x, y=edge_y,
        line=dict(width=0.5, color='#888'),
        hoverinfo='none',
        mode='lines')

    node_x = []
    node_y = []
    text = []

    for node in GRAPH.nodes():
        x, y = pos[node]
        node_x.append(x)
        node_y.append(y)
        node_data = GRAPH.nodes[node]
        text.append(f"ID: {node}<br>Capacity: {node_data['capacity']}<br>Usage: {node_data['usage']}<br>Time Cost: {node_data['time_cost']}")

    node_trace = go.Scatter(
        x=node_x, y=node_y,
        mode='markers',
        hoverinfo='text',
        text=text,
        marker=dict(
            showscale=True,
            colorscale='YlGnBu',
            size=10,
            colorbar=dict(
                thickness=15,
                title='Node Data',
                xanchor='left',
                titleside='right'
            ),
        )
    )

    fig = go.Figure(data=[edge_trace, node_trace],
                    layout=go.Layout(
                        showlegend=False,
                        hovermode='closest',
                        margin=dict(b=0, l=0, r=0, t=0),
                        xaxis=dict(showgrid=False, zeroline=False),
                        yaxis=dict(showgrid=False, zeroline=False)))
    return fig

# Initialize Dash app
app = dash.Dash(__name__)
app.layout = html.Div([
    dcc.Graph(id='graph-plot'),
    html.Div([
        html.Button('Previous Step', id='prev-step', n_clicks=0),
        html.Button('Next Step', id='next-step', n_clicks=0),
        html.Button('Load All', id='load-all', n_clicks=0),
        html.Button('Reset', id='reset', n_clicks=0)
    ]),
])

@app.callback(
    Output('graph-plot', 'figure'),
    [Input('prev-step', 'n_clicks'),
     Input('next-step', 'n_clicks'),
     Input('load-all', 'n_clicks'),
     Input('reset', 'n_clicks')]
)
def navigate_steps(prev_clicks, next_clicks, load_all_clicks, reset_clicks):
    global CURRENT_STEP, GRAPH

    ctx = dash.callback_context
    if not ctx.triggered:
        return create_graph_figure()

    button_id = ctx.triggered[0]['prop_id'].split('.')[0]

    if button_id == 'next-step':
        save_graph_state()
        CURRENT_STEP += 1
        step_row = csv_data.iloc[CURRENT_STEP]
        update_graph(step_row)

    elif button_id == 'prev-step' and CURRENT_STEP > 0:
        restore_previous_state()

    elif button_id == 'load-all':
        save_graph_state()
        GRAPH.clear()
        csv_data = pd.read_csv(CSV_FILE)
        for _, row in csv_data.iterrows():
            update_graph(row)

    elif button_id == 'reset':
        GRAPH.clear()
        CURRENT_STEP = 1
        UNDO_STACK.clear()
        update_graph(csv_data.iloc[0])

    return create_graph_figure()

# Process the first step
update_graph(csv_data.iloc[0])

if __name__ == '__main__':
    app.run_server(debug=True)
