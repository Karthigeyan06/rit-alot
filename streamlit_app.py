import streamlit as st
import pandas as pd
from fuzzywuzzy import fuzz
import sqlite3
import io

# ----------------- DATABASE SETUP -----------------
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

# ----------------- FAIR + FUZZY ALLOTMENT LOGIC -----------------
def match_buses_by_choice(choice, bus_data, threshold):
    matched = []
    for bus_name, bus in bus_data.items():
        for stop in bus['stops']:
            score = fuzz.ratio(choice, stop)
            if score >= threshold:
                matched.append((bus_name, stop, score))
    matched.sort(key=lambda x: -x[2])
    return matched

def try_allocate_student(student_id, choice, bus_data, c, threshold):
    for bus_name, matched_stop, _ in match_buses_by_choice(choice, bus_data, threshold):
        if bus_data[bus_name]['seats'] > 0:
            c.execute("""
                UPDATE students
                SET bus_allotted=?, allotted_stop=?
                WHERE id=?
            """, (bus_name, matched_stop, student_id))
            bus_data[bus_name]['seats'] -= 1
            bus_data[bus_name]['count'] += 1
            return True
    return False

def allocate_students_fair_fuzzy(c, bus_data, threshold=85):
    students = c.execute("SELECT id, choice1, choice2 FROM students").fetchall()

    single_option = []
    multi_option = []

    for sid, ch1, ch2 in students:
        buses_choice1 = match_buses_by_choice(ch1, bus_data, threshold)
        buses_choice2 = match_buses_by_choice(ch2, bus_data, threshold)
        total_possible = set([b[0] for b in buses_choice1] + [b[0] for b in buses_choice2])

        if len(total_possible) == 1:
            single_option.append((sid, ch1, ch2))
        else:
            multi_option.append((sid, ch1, ch2))

    # First single-option students
    for sid, ch1, ch2 in single_option:
        if not try_allocate_student(sid, ch1, bus_data, c, threshold):
            try_allocate_student(sid, ch2, bus_data, c, threshold)

    # Then multi-option students
    for sid, ch1, ch2 in multi_option:
        if not try_allocate_student(sid, ch1, bus_data, c, threshold):
            try_allocate_student(sid, ch2, bus_data, c, threshold)

# ----------------- STREAMLIT UI -----------------
st.set_page_config(page_title="Smart Bus Allotment System", layout="wide")
st.title("Smart Bus Allotment System")

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

            stops_list = df['Stoppings'].tolist()
            stops_str = ",".join(stops_list)  # store all stops in one column

            # Insert only ONE row per bus
            c.execute("INSERT INTO buses (bus_name, stoppings, seats_available) VALUES (?, ?, ?)",
                    (bus_name, stops_str, seats))

            bus_data[bus_name] = {
                'stops': stops_list,
                'seats': seats,
                'count': 0
            }

        conn.commit()

        # Run fair + fuzzy allotment
        allocate_students_fair_fuzzy(c, bus_data, threshold=85)
        conn.commit()

        st.success("Allotment Completed!")
        conn.close()

# ----------------- RETRIEVAL SECTION -----------------
st.subheader("📌 Data Retrieval Panel")
conn, c = init_db()

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

if not df_filtered.empty:
    csv_data = io.BytesIO()
    df_filtered.to_csv(csv_data, index=False)
    st.download_button("Download Filtered Data", csv_data.getvalue(),
                       "filtered_students.csv", "text/csv")

st.subheader("Bus Capacity Summary")
bus_summary = pd.read_sql_query("""
    SELECT b.bus_name,
           b.seats_available AS total_seats,
           COUNT(s.id) AS allotted_count,
           b.seats_available - COUNT(s.id) AS remaining_seats
    FROM buses b
    LEFT JOIN students s ON s.bus_allotted = b.bus_name
    GROUP BY b.bus_name
""", conn)

st.dataframe(bus_summary)
conn.close()
