import os
import random
import re
import mysql.connector
import nltk
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from quiz_generator import DynamicQuizGenerator
import PyPDF2
import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes

# Windows Tesseract Executable Path Setup
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Standard Web App Path Resolution
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(BASE_DIR, 'templates')
static_dir = os.path.join(BASE_DIR, 'static')

app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
app.secret_key = 'quiz_generator_secret_key'

def get_db_connection():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="280325",
        database="quiz_generator"
    )

def extract_text_from_pdf(pdf_file):
    text = ""

    # 1. Method 1: Extraction using pdfplumber
    try:
        pdf_file.seek(0)
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + " \n"
    except Exception as e:
        print(f"pdfplumber Extraction Error: {e}")

    # 2. Method 2: Fallback extraction using PyPDF2
    if not text.strip():
        try:
            pdf_file.seek(0)
            reader = PyPDF2.PdfReader(pdf_file)
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + " \n"
        except Exception as e:
            print(f"PyPDF2 Extraction Error: {e}")

    # 3. Method 3: OCR Extraction using pytesseract for Scanned/Image PDFs
    if not text.strip() or len(text.strip()) < 20:
        try:
            print("Extracting via OCR for scanned PDF...")
            pdf_file.seek(0)
            images = convert_from_bytes(pdf_file.read(), poppler_path=r'C:\poppler\Library\bin')
            ocr_text = ""
            for img in images:
                ocr_text += pytesseract.image_to_string(img) + " \n"
            text = ocr_text
        except Exception as e:
            print(f"OCR Extraction Error: {e}")

    # --- TEXT CLEANING & PARSING FIX ---
    if text:
        text = re.sub(r'\(cid:\d+\)', '', text)
        text = re.sub(r'(?i)(UNIT|CHAPTER|LESSON|SECTION)\s+\d+[:\.]?', '', text)
        text = re.sub(r'\b\d+\.\d+(\.\d+)?\b', '', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n+', '\n', text)

    return text.strip()

@app.route('/')
def home():
    session.clear()
    return redirect(url_for('login'))

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['name'] = user['name']
            session['role'] = user['role']
            
            if str(user['role']).lower() == 'teacher':
                return redirect(url_for('teacher_dashboard'))
            else:
                return redirect(url_for('student_dashboard'))
        else:
            flash('Invalid email or password!')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])
        role = request.form['role']

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO users (name, email, password, role) VALUES (%s, %s, %s, %s)",
                           (name, email, password, role))
            conn.commit()
            flash('Registration successful! Please login.')
            return redirect(url_for('login'))
        except mysql.connector.Error as err:
            flash(f'Error: {err}')
        finally:
            conn.close()

    return render_template('register.html')

@app.route('/teacher/dashboard')
def teacher_dashboard():
    if 'role' not in session or session['role'] != 'teacher':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT q.*, 
               COUNT(a.id) as total_attempts,
               COUNT(DISTINCT COALESCE(a.user_id, a.student_id)) as unique_students
        FROM quizzes q
        LEFT JOIN attempts a ON q.id = a.quiz_id
        WHERE q.created_by = %s
        GROUP BY q.id
        ORDER BY q.id DESC
    """, (session['user_id'],))
    
    quizzes = cursor.fetchall()
    conn.close()

    return render_template('teacher_dashboard.html', quizzes=quizzes)

@app.route('/generate_quiz', methods=['POST'])
def generate_quiz():
    if 'role' not in session or session['role'] != 'teacher':
        return redirect(url_for('login'))

    title = request.form.get('title', 'Untitled Quiz').strip()
    topic = request.form.get('topic', 'General').strip()
    num_questions = int(request.form.get('num_questions', 5))
    time_limit = int(request.form.get('time_limit', 10))
    
    extracted_text = ""

    if 'pdf_file' in request.files:
        pdf_file = request.files['pdf_file']
        if pdf_file and pdf_file.filename != '':
            extracted_text = extract_text_from_pdf(pdf_file)

    text_input = request.form.get('text_input', '').strip()
    if not extracted_text and text_input:
        extracted_text = text_input

    if not extracted_text or len(extracted_text) < 10:
        flash('Could not extract readable text from PDF! Try pasting text directly into Option 2.')
        return redirect(url_for('teacher_dashboard'))

    try:
        generated_mcqs = DynamicQuizGenerator.generate_unique_mcqs(
            text=extracted_text, 
            topic=topic, 
            title=title, 
            num_questions=num_questions
        )

        if generated_mcqs:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "INSERT INTO quizzes (title, topic, created_by, time_limit, status) VALUES (%s, %s, %s, %s, %s)",
                (title, topic, session['user_id'], time_limit, 'published')
            )
            quiz_id = cursor.lastrowid

            for mcq in generated_mcqs:
                cursor.execute("""
                    INSERT INTO questions (quiz_id, question, option_a, option_b, option_c, option_d, answer)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (quiz_id, mcq['question'], mcq['option_a'], mcq['option_b'], mcq['option_c'], mcq['option_d'], mcq['answer']))

            conn.commit()
            conn.close()
            flash(f'Success! Quiz created with {len(generated_mcqs)} questions.')
        else:
            flash('Failed to generate MCQs from text. Content might be too short.')

    except Exception as e:
        print(f"Error: {e}")
        flash(f'Error generating quiz: {str(e)}')

    return redirect(url_for('teacher_dashboard'))

@app.route('/teacher/quiz_results/<int:quiz_id>')
def quiz_results(quiz_id):
    if 'role' not in session or session['role'] != 'teacher':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM quizzes WHERE id = %s AND created_by = %s", (quiz_id, session['user_id']))
    quiz = cursor.fetchone()

    if not quiz:
        conn.close()
        flash('Quiz not found or unauthorized access!')
        return redirect(url_for('teacher_dashboard'))

    cursor.execute("SELECT COUNT(*) as total_questions FROM questions WHERE quiz_id = %s", (quiz_id,))
    q_count = cursor.fetchone()
    total_questions = q_count['total_questions'] if q_count else 0

    cursor.execute("""
        SELECT 
            u.id as student_id,
            u.name as student_name,
            u.email as student_email,
            a.score,
            a.attempted_at
        FROM attempts a
        JOIN users u ON (a.user_id = u.id OR a.student_id = u.id)
        WHERE a.quiz_id = %s
        ORDER BY a.score DESC, a.attempted_at DESC
    """, (quiz_id,))
    
    results = cursor.fetchall()

    total_students = len(results)
    avg_score = round(sum(r['score'] for r in results) / total_students, 2) if total_students > 0 else 0

    conn.close()

    return render_template(
        'quiz_results.html', 
        quiz=quiz, 
        results=results, 
        total_questions=total_questions,
        total_students=total_students,
        avg_score=avg_score
    )

@app.route('/edit_quiz/<int:quiz_id>', methods=['GET', 'POST'])
def edit_quiz(quiz_id):
    if 'role' not in session or session['role'] != 'teacher':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM quizzes WHERE id = %s AND created_by = %s", (quiz_id, session['user_id']))
    quiz = cursor.fetchone()

    if not quiz:
        conn.close()
        flash('Quiz not found or unauthorized access!')
        return redirect(url_for('teacher_dashboard'))

    if request.method == 'POST':
        title = request.form.get('title', quiz['title']).strip()
        topic = request.form.get('topic', quiz['topic']).strip()
        time_limit = int(request.form.get('time_limit', quiz['time_limit']))

        cursor.execute("""
            UPDATE quizzes 
            SET title = %s, topic = %s, time_limit = %s 
            WHERE id = %s
        """, (title, topic, time_limit, quiz_id))

        cursor.execute("SELECT id FROM questions WHERE quiz_id = %s", (quiz_id,))
        existing_questions = cursor.fetchall()

        for q in existing_questions:
            q_id = q['id']
            question_text = request.form.get(f"question_{q_id}")
            opt_a = request.form.get(f"option_a_{q_id}")
            opt_b = request.form.get(f"option_b_{q_id}")
            opt_c = request.form.get(f"option_c_{q_id}")
            opt_d = request.form.get(f"option_d_{q_id}")
            answer = request.form.get(f"answer_{q_id}")

            if question_text and answer:
                cursor.execute("""
                    UPDATE questions 
                    SET question = %s, option_a = %s, option_b = %s, option_c = %s, option_d = %s, answer = %s 
                    WHERE id = %s AND quiz_id = %s
                """, (question_text, opt_a, opt_b, opt_c, opt_d, answer, q_id, quiz_id))

        conn.commit()
        conn.close()
        flash('Quiz updated successfully!')
        return redirect(url_for('teacher_dashboard'))

    cursor.execute("SELECT * FROM questions WHERE quiz_id = %s", (quiz_id,))
    questions = cursor.fetchall()
    conn.close()

    return render_template('edit_quiz.html', quiz=quiz, questions=questions)

@app.route('/delete_quiz/<int:quiz_id>', methods=['POST'])
def delete_quiz(quiz_id):
    if 'role' not in session or session['role'] != 'teacher':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM attempts WHERE quiz_id = %s", (quiz_id,))
    cursor.execute("DELETE FROM questions WHERE quiz_id = %s", (quiz_id,))
    cursor.execute("DELETE FROM quizzes WHERE id = %s AND created_by = %s", (quiz_id, session['user_id']))
    
    conn.commit()
    conn.close()
    
    flash('Quiz deleted successfully!')
    return redirect(url_for('teacher_dashboard'))

@app.route('/student/dashboard')
def student_dashboard():
    if 'role' not in session or session['role'] != 'student':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM quizzes WHERE status='published' ORDER BY id DESC")
    quizzes = cursor.fetchall()

    # Get list of quiz IDs already attempted by this student
    cursor.execute("SELECT DISTINCT quiz_id FROM attempts WHERE user_id = %s OR student_id = %s", (session['user_id'], session['user_id']))
    attended_rows = cursor.fetchall()
    attended_quiz_ids = [row['quiz_id'] for row in attended_rows]

    cursor.execute("SELECT COUNT(*) as total FROM attempts WHERE user_id = %s OR student_id = %s", (session['user_id'], session['user_id']))
    total_res = cursor.fetchone()
    total_attended = total_res['total'] if total_res and total_res['total'] else 0

    cursor.execute("SELECT AVG(score) as avg_s FROM attempts WHERE user_id = %s OR student_id = %s", (session['user_id'], session['user_id']))
    avg_res = cursor.fetchone()
    avg_score = round(avg_res['avg_s'], 1) if avg_res and avg_res['avg_s'] else 0

    cursor.execute("""
        SELECT users.name, COALESCE(SUM(attempts.score), 0) as total_score 
        FROM attempts 
        JOIN users ON (attempts.user_id = users.id OR attempts.student_id = users.id)
        GROUP BY users.id, users.name 
        ORDER BY total_score DESC LIMIT 5
    """)
    leaderboard = cursor.fetchall()

    conn.close()

    return render_template('student_dashboard.html', 
                           quizzes=quizzes, 
                           attended_quiz_ids=attended_quiz_ids,
                           total_attended=total_attended, 
                           avg_score=avg_score, 
                           leaderboard=leaderboard)

@app.route('/quiz/<int:quiz_id>')
def take_quiz(quiz_id):
    if 'role' not in session or session['role'] != 'student':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM quizzes WHERE id = %s", (quiz_id,))
    quiz = cursor.fetchone()

    if not quiz:
        conn.close()
        flash("Quiz not found!")
        return redirect(url_for('student_dashboard'))

    cursor.execute("SELECT * FROM questions WHERE quiz_id = %s", (quiz_id,))
    questions = cursor.fetchall()
    conn.close()

    return render_template('take_quiz.html', quiz=quiz, questions=questions)

@app.route('/submit_quiz/<int:quiz_id>', methods=['POST'])
def submit_quiz(quiz_id):
    if 'role' not in session or session['role'] != 'student':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM quizzes WHERE id = %s", (quiz_id,))
    quiz = cursor.fetchone()

    cursor.execute("SELECT * FROM questions WHERE quiz_id = %s", (quiz_id,))
    questions = cursor.fetchall()

    score = 0
    total_questions = len(questions)
    details = []

    for question in questions:
        field_name = f"question_{question['id']}"
        selected_option = request.form.get(field_name, '').strip()
        db_answer = str(question['answer']).strip()

        options_map = {
            'A': str(question.get('option_a', '')).strip(),
            'B': str(question.get('option_b', '')).strip(),
            'C': str(question.get('option_c', '')).strip(),
            'D': str(question.get('option_d', '')).strip()
        }

        correct_option_letter = ""
        correct_ans_text = ""

        if db_answer.upper() in ['A', 'B', 'C', 'D']:
            correct_option_letter = db_answer.upper()
            correct_ans_text = options_map.get(correct_option_letter, '')
        else:
            for letter, text in options_map.items():
                if text.lower() == db_answer.lower():
                    correct_option_letter = letter
                    correct_ans_text = text
                    break

        if not correct_option_letter:
            correct_ans_text = db_answer

        is_correct = False
        if selected_option:
            sel_upper = selected_option.upper()
            if sel_upper == correct_option_letter:
                is_correct = True
            elif sel_upper in options_map and options_map[sel_upper].lower() == db_answer.lower():
                is_correct = True

        if is_correct:
            score += 1

        user_ans_text = options_map.get(selected_option.upper(), "Not Answered") if selected_option else "Not Answered"

        details.append({
            'question': question['question'],
            'selected_answer': f"{selected_option.upper()} - {user_ans_text}" if selected_option else "Not Answered",
            'correct_answer': f"{correct_option_letter or 'Correct'} - {correct_ans_text}",
            'is_correct': is_correct
        })

    try:
        cursor.execute("""
            INSERT INTO attempts (user_id, student_id, quiz_id, score) 
            VALUES (%s, %s, %s, %s)
        """, (session['user_id'], session['user_id'], quiz_id, score))
        conn.commit()
    except mysql.connector.Error as err:
        print(f"Database Error on Submit: {err}")
    finally:
        conn.close()

    percentage = round((score / total_questions) * 100, 1) if total_questions > 0 else 0

    attempt_data = {
        'score': percentage,
        'marks': f"{score}/{total_questions}"
    }

    return render_template('result.html', quiz=quiz, attempt=attempt_data, details=details)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)