import streamlit as st
import pandas as pd
from fuzzywuzzy import fuzz
import sqlite3
import io

# SQLite setup
def init_db():
    conn = sqlite3.connect("transport.db")
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        year INTEGER,
        department TEXT,
        choice1 TEXT,
        choice2 TEXT,
        bus_allotted TEXT,
        allotted_stop TEXT
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS buses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bus_name TEXT,
        stoppings TEXT,
        seats_available INTEGER
    )
    """)
    conn.commit()
    return conn, c

# Allotment logic
def try_allot(stop, student_id, bus_data, c, threshold=85):
    matched_buses = []
    for bus_name, bus in bus_data.items():
        for bus_stop in bus['stops']:
            score = fuzz.ratio(stop, bus_stop)
            if score >= threshold:
                matched_buses.append((bus_name, bus_stop, score))
                break
    matched_buses.sort(key=lambda x: -x[2])
    for bus_name, matched_stop, _ in matched_buses:
        if bus_data[bus_name]['seats'] > 0:
            c.execute("UPDATE students SET bus_allotted=?, allotted_stop=? WHERE id=?",
                      (bus_name, matched_stop, student_id))
            bus_data[bus_name]['seats'] -= 1
            bus_data[bus_name]['count'] += 1
            return True
    return False

# Streamlit UI
st.set_page_config(page_title="Smart Bus Allotment System", layout="wide")
st.title("🚌 Smart Bus Allotment System (Streamlit + SQLite)")

# File uploads
student_file = st.file_uploader("📄 Upload Student CSV", type=["csv"])
bus_files = st.file_uploader("📂 Upload Bus CSV Files", type=["csv"], accept_multiple_files=True)

if st.button("Run Allotment"):
    if not student_file or not bus_files:
        st.error("Please upload student file and bus files.")
    else:
        conn, c = init_db()

        # Clear old data
        c.execute("DELETE FROM students")
        c.execute("DELETE FROM buses")
        conn.commit()

        # Load student CSV
        studentdata = pd.read_csv(student_file)
        required_cols = ['Name', 'Year', 'Department', 'Choice 1', 'Choice 2']
        if not all(col in studentdata.columns for col in required_cols):
            st.error(f"Student CSV missing required columns: {', '.join(required_cols)}")
            st.stop()

        studentdata['Choice 1'] = studentdata['Choice 1'].astype(str).str.strip().str.lower()
        studentdata['Choice 2'] = studentdata['Choice 2'].astype(str).str.strip().str.lower()

        for _, row in studentdata.iterrows():
            c.execute("""
                INSERT INTO students (name, year, department, choice1, choice2, bus_allotted, allotted_stop)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                row['Name'],
                int(row['Year']),
                row['Department'],
                row['Choice 1'],
                row['Choice 2'],
                'None',
                'None'
            ))
        conn.commit()

        # Load buses
        bus_data = {}
        for file in bus_files:
            bus_name = file.name.replace(".csv", "")
            df = pd.read_csv(file)
            df['Stoppings'] = df['Stoppings'].astype(str).str.strip().str.lower()
            seats = int(df['Seats Available'].iloc[0]) if pd.notna(df['Seats Available'].iloc[0]) else 0

            for stop in df['Stoppings']:
                c.execute("INSERT INTO buses (bus_name, stoppings, seats_available) VALUES (?, ?, ?)",
                          (bus_name, stop, seats))

            bus_data[bus_name] = {
                'stops': df['Stoppings'].tolist(),
                'seats': seats,
                'count': 0
            }

        conn.commit()

        # Allotment
        students = c.execute("SELECT id, choice1, choice2 FROM students").fetchall()
        for sid, ch1, ch2 in students:
            if not try_allot(ch1, sid, bus_data, c):
                try_allot(ch2, sid, bus_data, c)
        conn.commit()

        st.success("✅ Allotment Completed!")

        conn.close()

# ----------------- RETRIEVAL SECTION -----------------
st.subheader("📌 Data Retrieval Panel")

conn, c = init_db()

# Sidebar filters
with st.sidebar:
    st.header("🔍 Filters")
    years = [row[0] for row in c.execute("SELECT DISTINCT year FROM students ORDER BY year").fetchall()]
    depts = [row[0] for row in c.execute("SELECT DISTINCT department FROM students ORDER BY department").fetchall()]
    buses = [row[0] for row in c.execute("SELECT DISTINCT bus_allotted FROM students ORDER BY bus_allotted").fetchall()]

    year_filter = st.multiselect("Year", options=years)
    dept_filter = st.multiselect("Department", options=depts)
    bus_filter = st.multiselect("Bus", options=buses)
    stop_filter = st.text_input("Stop Name (contains)")
    unallotted_only = st.checkbox("Show only Unallotted")

# Build query dynamically
query = "SELECT name, year, department, choice1, choice2, bus_allotted, allotted_stop FROM students WHERE 1=1"
params = []

if year_filter:
    query += f" AND year IN ({','.join(['?']*len(year_filter))})"
    params.extend(year_filter)
if dept_filter:
    query += f" AND department IN ({','.join(['?']*len(dept_filter))})"
    params.extend(dept_filter)
if bus_filter:
    query += f" AND bus_allotted IN ({','.join(['?']*len(bus_filter))})"
    params.extend(bus_filter)
if stop_filter:
    query += " AND allotted_stop LIKE ?"
    params.append(f"%{stop_filter.lower()}%")
if unallotted_only:
    query += " AND bus_allotted='None'"

df_filtered = pd.read_sql_query(query, conn, params=params)

st.dataframe(df_filtered)

# Download button
if not df_filtered.empty:
    csv_data = io.BytesIO()
    df_filtered.to_csv(csv_data, index=False)
    st.download_button("📥 Download Filtered Data", csv_data.getvalue(),
                       "filtered_students.csv", "text/csv")

# Bus summary
st.subheader("🚌 Bus Capacity Summary")
bus_summary = pd.read_sql_query("""
    SELECT bus_name,
           MAX(seats_available) AS total_seats,
           SUM(CASE WHEN s.bus_allotted = b.bus_name THEN 1 ELSE 0 END) AS allotted_count,
           MAX(seats_available) - SUM(CASE WHEN s.bus_allotted = b.bus_name THEN 1 ELSE 0 END) AS remaining_seats
    FROM buses b
    LEFT JOIN students s ON s.bus_allotted = b.bus_name
    GROUP BY bus_name
""", conn)
st.dataframe(bus_summary)

conn.close()
