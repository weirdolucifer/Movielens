import os
from pyspark.mllib.recommendation import ALS, MatrixFactorizationModel, Rating
import numpy as np
from numpy import linalg as LA
import logging
from pyspark.sql import functions as F

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


import pandas as pd

def cosineSimilarity(vec1, vec2):
  return vec1.dot(vec2) / (LA.norm(vec1) * LA.norm(vec2))

def get_counts_and_averages(ID_and_ratings_tuple):
    """Given a tuple (movieID, ratings_iterable) 
    returns (movieID, (ratings_count, ratings_avg))
    """
    nratings = len(ID_and_ratings_tuple[1])
    return ID_and_ratings_tuple[0], (nratings, float(sum(x for x in ID_and_ratings_tuple[1]))/nratings)



class RecommendationEngine:
    """A movie recommendation engine
    """

    def __count_and_average_ratings(self):
        """Updates the movies ratings counts from 
        the current data self.ratings_RDD
        """
        logger.info("Counting movie ratings...")
        movie_ID_with_ratings_RDD = self.ratings_RDD.map(lambda x: (x[1], x[2])).groupByKey()
        movie_ID_with_avg_ratings_RDD = movie_ID_with_ratings_RDD.map(get_counts_and_averages)
        self.movies_rating_counts_RDD = movie_ID_with_avg_ratings_RDD.map(lambda x: (x[0], x[1][0]))


    def __train_model(self):
        """Train the ALS model with the current dataset
        """
        logger.info("Training the ALS model...")
        self.model = ALS.train(self.ratings_RDD, self.rank, seed=self.seed,
                               iterations=self.iterations, lambda_=self.regularization_parameter)
        logger.info("ALS model built!")


    def __predict_ratings(self, user_and_movie_RDD):
        """Gets predictions for a given (userID, movieID) formatted RDD
        Returns: an RDD with format (movieTitle, movieRating, numRatings)
        """
        predicted_RDD = self.model.predictAll(user_and_movie_RDD)
        
        predicted_rating_RDD = predicted_RDD.map(lambda x: (x.product, x.rating))

        predicted_rating_title_and_count_RDD = \
            predicted_rating_RDD.join(self.movies_titles_RDD).join(self.movies_rating_counts_RDD).join(self.links_titles_RDD)
        

        predicted_rating_title_and_count_RDD = \
            predicted_rating_title_and_count_RDD.map(lambda r: (r[0] , r[1][0][0][1], r[1][0][0][0],r[1][0][1], r[1][1]  ))
        
        return predicted_rating_title_and_count_RDD
    
    def add_ratings(self, ratings1):
        """Add additional movie ratings in the format (user_id, movie_id, rating)
        """
        # Convert ratings to an RDD
        new_ratings_RDD = self.sc.parallelize(ratings1)
        
        self.ratings_RDD = self.ratings_RDD.union(new_ratings_RDD)
        self.__count_and_average_ratings()
        self.__train_model()
        
        return ratings1

    def get_ratings_for_movie_ids(self, user_id, movie_ids):
        """Given a user_id and a list of movie_ids, predict ratings for them 
        """
        requested_movies_RDD = self.sc.parallelize(movie_ids).map(lambda x: (user_id, x))
        # Get predicted ratings
        ratings = self.__predict_ratings(requested_movies_RDD).collect()
        df2 = pd.DataFrame(ratings)
        print(df2)
        return df2
    
    def get_top_ratings(self, user_id, movies_count):
        """Recommends up to movies_count top unrated movies to user_id
        """
        # Get pairs of (userID, movieID) for user_id unrated movies
        user_unrated_movies_RDD = self.ratings_RDD.filter(lambda rating: not rating[0] == user_id)\
                                                 .map(lambda x: (user_id, x[1])).distinct()
        # Get predicted ratings
        ratings = self.__predict_ratings(user_unrated_movies_RDD).filter(lambda r: r[3]>=25).takeOrdered(movies_count, key=lambda x: -x[2])
        df1 = pd.DataFrame(ratings)
        print (df1)
        
        return df1

    def get_user_ratings(self, user_id):
        
        user_rated_movies_RDD = self.ratings_RDD.filter(lambda rating: rating[0] == user_id)\
                                                 .map(lambda x: (x[1] , x[2])).distinct()

        ratings_RDD= \
            user_rated_movies_RDD.join(self.movies_titles_RDD).join(self.links_titles_RDD)
        

        new_ratings_RDD = \
           ratings_RDD.map(lambda r: (r[0] , r[1][0][1], r[1][0][0] , r[1][1]))
        
        
        df5 = pd.DataFrame(new_ratings_RDD.collect())
        print (df5)
        
        return df5    

    def get_recommend_for_movie_id(self,movie_id):
    
        # Calculates the nearest 20 movies for the given Movie ID
        logger.info("Inside Movie Recommend...")
        #movie_id = 20
        new_product_feature_RDD = self.product_feature_RDD.join(self.links_titles_RDD)
       
        
        complete_itemFactor = np.asarray(self.product_feature_RDD.lookup(movie_id))[0][0][0]
        
        complete_sims = new_product_feature_RDD.map(lambda products:(products[1][0][0][1],\
                                            cosineSimilarity(np.asarray(products[1][0][0][0]), complete_itemFactor),\
                                                   products[0],products[1][0][1][0],products[1][0][1][1],products[1][1]))
        

        complete_sortedSims = complete_sims.filter(lambda r: r[3]>=5).takeOrdered(21, key=lambda x: -x[1])
        
        
        df4 = pd.DataFrame(complete_sortedSims)
        print(df4)
        return df4    

    def __init__(self, sc, dataset_path):
        """Init the recommendation engine given a Spark context and a dataset path
        """

        logger.info("Starting up the Recommendation Engine: ")

        self.sc = sc
        
        # Load item ratings data for later use
        logger.info("Loading Model Features...")
        features_file_path = os.path.join(dataset_path,'product_40feature_rating')
        self.product_feature_RDD = self.sc.pickleFile(features_file_path)


        # Load ratings data for later use
        logger.info("Loading Ratings data...")
        ratings_file_path = os.path.join(dataset_path, 'ratings.csv')
        ratings_raw_RDD = self.sc.textFile(ratings_file_path)
        ratings_raw_data_header = ratings_raw_RDD.take(1)[0]
        self.ratings_RDD = ratings_raw_RDD.filter(lambda line: line!=ratings_raw_data_header)\
            .map(lambda line: line.split(",")).map(lambda tokens: (int(tokens[0]),int(tokens[1]),float(tokens[2]))).cache()
       
        #load links data for later use
        logger.info("Loading Links data...")
        links_file_path = os.path.join(dataset_path, 'links.csv')
        links_raw_RDD = self.sc.textFile(links_file_path)
        links_raw_data_header = links_raw_RDD.take(1)[0]
        self.links_RDD = links_raw_RDD.filter(lambda line: line!=links_raw_data_header)\
            .map(lambda line: line.split(",")).map(lambda tokens: (int(tokens[0]),tokens[1],tokens[2])).cache()
        self.links_titles_RDD = self.links_RDD.map(lambda x: (int(x[0]),x[1])).cache()
       
       
        # Load movies data for later use
        logger.info("Loading Movies data...")
        movies_file_path = os.path.join(dataset_path, 'movies.csv')
        movies_raw_RDD = self.sc.textFile(movies_file_path)
        movies_raw_data_header = movies_raw_RDD.take(1)[0]
        self.movies_RDD = movies_raw_RDD.filter(lambda line: line!=movies_raw_data_header)\
            .map(lambda line: line.split(",")).map(lambda tokens: (int(tokens[0]),tokens[1],tokens[2])).cache()
        self.movies_titles_RDD = self.movies_RDD.map(lambda x: (int(x[0]),x[1])).cache()
        # Pre-calculate movies ratings counts
        self.__count_and_average_ratings()

        # Train the model
        self.rank = 8
        self.seed = 5L
        self.iterations = 10
        self.regularization_parameter = 0.1
        self.__train_model() 
