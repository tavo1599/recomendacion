from flask import Flask, request, jsonify, render_template
import mysql.connector
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import requests
from rapidfuzz import process

app = Flask(__name__)

# Configuración de la base de datos
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',  # Cambia esto por tu contraseña de MySQL
    'database': 'movie_recommendation'
}

# Función para conectar con la base de datos
def get_db_connection():
    try:
        return mysql.connector.connect(**db_config)
    except mysql.connector.Error as e:
        print(f"Error conectando a la base de datos: {e}")
        raise

# Función para obtener el póster desde la base de datos o desde OMDb y actualizarlo
def get_or_save_poster(movie_title):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Verificar si el póster ya está en la base de datos
        cursor.execute("SELECT poster FROM movies WHERE title = %s", (movie_title,))
        result = cursor.fetchone()

        if result and result['poster']:
            return result['poster']

        # Si no está, solicitar el póster a OMDb
        response = requests.get(f"http://www.omdbapi.com/?t={movie_title}&apikey=dba2acd3")
        data = response.json()
        print(f"OMDb Response for '{movie_title}': {data}")

        if data.get('Response') == 'True':
            poster = data.get('Poster', 'https://via.placeholder.com/150')
            # Guardar el póster en la base de datos
            cursor.execute("UPDATE movies SET poster = %s WHERE title = %s", (poster, movie_title))
            conn.commit()
        else:
            poster = 'https://via.placeholder.com/150'

    except Exception as e:
        print(f"Error al obtener el póster para {movie_title}: {e}")
        poster = 'https://via.placeholder.com/150'
    
    finally:
        cursor.close()  # Asegúrate de cerrar el cursor después de usarlo
        conn.close()    # Cierra la conexión a la base de datos

    return poster

# Ruta principal para servir el frontend
@app.route('/')
def index():
    return render_template('index.html')

# Ruta para obtener todas las películas
@app.route('/movies', methods=['GET'])
def get_movies():
    try:
        genre = request.args.get('genre')  # Permite filtrar por género si se proporciona
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Consulta filtrada por género si se especifica
        if genre:
            query = "SELECT id, title, genre, description, duration, language, poster FROM movies WHERE genre LIKE %s"
            cursor.execute(query, (f"%{genre}%",))
        else:
            query = "SELECT id, title, genre, description, duration, language, poster FROM movies"
            cursor.execute(query)

        movies = cursor.fetchall()

        # No es necesario verificar o actualizar pósters si ya están en la base de datos
        cursor.close()
        conn.close()

        return jsonify(movies)

    except Exception as e:
        print(f"Error en la ruta /movies: {e}")  # Mostrar el error en los logs
        return jsonify({'error': 'Error al obtener las películas.', 'details': str(e)}), 500

    
# Ruta para obtener recomendaciones
@app.route('/recommend', methods=['GET'])
def recommend():
    movie_title = request.args.get('title')
    if not movie_title:
        return jsonify({'error': 'Por favor, proporciona el título de una película.'}), 400

    try:
        # Conectar a la base de datos y cargar las películas
        conn = get_db_connection()
        
        # Usar un bloque `with` para gestionar automáticamente el cursor y evitar problemas de "Unread result found"
        with conn.cursor(dictionary=True) as cursor:
            # Ejecutar la consulta para obtener todas las películas
            cursor.execute('SELECT id, title, genre, description, duration, language, poster FROM movies')
            movies = cursor.fetchall()

            if not movies:
                return jsonify({'error': 'No se encontraron películas en la base de datos.'}), 404

            # Convertir los datos a un DataFrame de pandas
            df = pd.DataFrame(movies)

        # Encuentra el título más cercano al texto proporcionado
        titles = df['title'].tolist()
        closest_match = process.extractOne(movie_title, titles)  # Encuentra la mejor coincidencia

        if closest_match is None or closest_match[1] < 70:  # Configura un umbral (por ejemplo, 70%)
            return jsonify({'error': f'No se encontró ninguna película similar a "{movie_title}".'}), 404

        # Obtener el título más cercano
        matched_title = closest_match[0]
        print(f"Se ha encontrado el título más cercano: {matched_title}")

        # Información de la película buscada
        searched_movie = df[df['title'] == matched_title].to_dict('records')[0]
        searched_movie['poster'] = get_or_save_poster(matched_title)

        # Crear una columna combinada con las características relevantes
        df['combined_features'] = df['genre'] + " " + df['description']

        # Vectorizar las características combinadas
        tfidf = TfidfVectorizer(stop_words='english')
        tfidf_matrix = tfidf.fit_transform(df['combined_features'])

        # Calcular la similitud de coseno
        cosine_sim = cosine_similarity(tfidf_matrix, tfidf_matrix)

        # Encontrar el índice de la película
        idx = df[df['title'] == matched_title].index[0]

        # Obtener las películas más similares
        sim_scores = list(enumerate(cosine_sim[idx]))
        sim_scores = sorted(sim_scores, key=lambda x: x[1], reverse=True)
        sim_scores = sim_scores[1:6]  # Las 5 películas más similares

        # Obtener los títulos de las películas recomendadas
        recommended_movies = df.iloc[[i[0] for i in sim_scores]].to_dict('records')

        # Agregar pósters a las recomendaciones
        for movie in recommended_movies:
            movie['poster'] = get_or_save_poster(movie['title'])

        return jsonify({
            'searched_movie': searched_movie,
            'recommended_movies': recommended_movies
        })

    except Exception as e:
        print(f"Error en la ruta /recommend: {e}")  # Mostrar el error en los logs
        return jsonify({'error': 'Error al obtener recomendaciones.', 'details': str(e)}), 500



    
@app.route('/dashboard', methods=['GET'])
def get_dashboard_data():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Total de películas
        cursor.execute("SELECT COUNT(*) AS total_movies FROM movies")
        total_movies = cursor.fetchone()['total_movies']

        # Películas por género
        cursor.execute("SELECT genre, COUNT(*) AS count FROM movies GROUP BY genre")
        movies_by_genre = cursor.fetchall()

        # Películas por director
        cursor.execute("SELECT director, COUNT(*) AS count FROM movies GROUP BY director")
        movies_by_director = cursor.fetchall()

        # Películas por año
        cursor.execute("SELECT release_year AS year, COUNT(*) AS count FROM movies GROUP BY release_year ORDER BY release_year")
        movies_by_year = cursor.fetchall()

        # Películas por duración (agrupadas en rangos)
        cursor.execute("""
            SELECT 
                CASE 
                    WHEN duration < 60 THEN 'Less than 60 mins'
                    WHEN duration BETWEEN 60 AND 90 THEN '60-90 mins'
                    WHEN duration BETWEEN 91 AND 120 THEN '91-120 mins'
                    ELSE 'More than 120 mins'
                END AS duration_range, 
                COUNT(*) AS count 
            FROM movies 
            GROUP BY duration_range
        """)
        movies_by_duration = cursor.fetchall()

        # Películas por idioma
        cursor.execute("SELECT language, COUNT(*) AS count FROM movies GROUP BY language")
        movies_by_language = cursor.fetchall()

        conn.close()

        return jsonify({
            'total_movies': total_movies,
            'movies_by_genre': movies_by_genre,
            'movies_by_director': movies_by_director,
            'movies_by_year': movies_by_year,
            'movies_by_duration': movies_by_duration,
            'movies_by_language': movies_by_language
        })

    except Exception as e:
        print(f"Error en la ruta /dashboard: {e}")
        return jsonify({'error': 'Error al obtener datos del dashboard.', 'details': str(e)}), 500
if __name__ == '__main__':
    app.run(debug=True)
