import streamlit as st
from main import main

st.title("Credit Card Statements Summary")
search_button = st.button("Start Search")
if search_button:
    main()
