import random
import re
import nltk

try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

class DynamicQuizGenerator:

    @staticmethod
    def preprocess_text(text):
        """Cleans PDF noise, headings, bullet symbols, and section numbers."""
        # 1. Remove bullets, quotes, and special symbol noise
        text = re.sub(r'[•\t\r]', ' ', text)
        text = re.sub(r'\(cid:\d+\)', '', text)

        # 2. Remove section numbers (e.g. 1.1.2, 2.4.1) and all-caps titles
        text = re.sub(r'\b\d+(\.\d+)+\b', '', text)
        text = re.sub(r'(?i)\b(UNIT|CHAPTER|LESSON|SECTION|CHARACTERISTICS|INTRODUCTION|DEFINITION|APPLICATIONS)\b', '', text)

        # 3. Clean spaces
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    @staticmethod
    def generate_unique_mcqs(text, topic="General", title="Quiz", num_questions=5):
        cleaned_text = DynamicQuizGenerator.preprocess_text(text)
        raw_sentences = nltk.sent_tokenize(cleaned_text)

        # Filter out junk chunks, headings, or merged paragraphs
        clean_sentences = []
        for s in raw_sentences:
            s_clean = s.strip()
            words = s_clean.split()
            # Only pick proper sentence sizes (8 to 25 words) to avoid oversized combined text
            if 8 <= len(words) <= 25 and not s_clean.isupper():
                clean_sentences.append(s_clean)

        generated_mcqs = []
        
        topic_words = set(re.findall(r'\w+', (topic or "") + " " + (title or "")))
        ignore_words = {
            "the", "a", "an", "is", "are", "was", "were", "and", "or", "in", "on", "at", 
            "to", "for", "of", "with", "by", "quiz", "test", "this", "that", "it", "from", "be"
        }
        topic_keywords = {w.lower() for w in topic_words if w.lower() not in ignore_words}

        priority_sentences = []
        other_sentences = []

        for s in clean_sentences:
            if any(kw in s.lower() for kw in topic_keywords):
                priority_sentences.append(s)
            else:
                other_sentences.append(s)

        random.shuffle(priority_sentences)
        random.shuffle(other_sentences)
        
        all_selected_sentences = priority_sentences + other_sentences

        all_words = list(set([w.strip(".,()\"';:") for w in cleaned_text.split() if len(w) > 3 and w.lower() not in ignore_words]))

        for sentence in all_selected_sentences:
            if len(generated_mcqs) >= num_questions:
                break

            words = [w.strip(".,()\"';:") for w in sentence.split()]
            candidates = [w for w in words if len(w) > 3 and w.lower() not in ignore_words and w.isalpha()]

            if not candidates:
                continue

            correct_answer = random.choice(candidates)
            
            pattern = re.compile(r'\b' + re.escape(correct_answer) + r'\b', re.IGNORECASE)
            question_text = pattern.sub("_______", sentence, count=1)

            # Avoid generating if replacement failed
            if "_______" not in question_text:
                continue

            distractors = [w for w in all_words if w.lower() != correct_answer.lower() and w.isalpha()]
            if len(distractors) < 3:
                distractors.extend(["System", "Process", "Data", "Network"])

            wrong_options = random.sample(distractors, 3)
            options = wrong_options + [correct_answer]
            random.shuffle(options)

            generated_mcqs.append({
                "question": question_text,
                "option_a": options[0],
                "option_b": options[1],
                "option_c": options[2],
                "option_d": options[3],
                "answer": correct_answer
            })

        return generated_mcqs