#!/usr/bin/env python
#
###############################################################################
# Author: Greg Zynda
# Last Modified: 05/19/2019
###############################################################################
# BSD 3-Clause License
# 
# Copyright (c) 2019, Texas Advanced Computing Center
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
# 
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# 
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
###############################################################################

#!pip install hmmlearn &> /dev/null
import numpy as np
import os, psutil
#from hmmlearn import hmm
os.putenv('TF_CPP_MIN_LOG_LEVEL','3')
import tensorflow as tf
tf.logging.set_verbosity(tf.logging.ERROR)
from tensorflow.python.client import device_lib
from tensorflow.keras.backend import set_session
from tensorflow.keras.models import load_model, Sequential
from tensorflow.keras.layers import Bidirectional, LSTM, RNN, Dense, CuDNNLSTM 
from tensorflow.keras.regularizers import l1, l2, l1_l2
from tensorflow.keras.optimizers import RMSprop, Adam
try:
	import horovod.tensorflow as hvd
	#hvd.init() # Only need to do this once
except:
	hvd = False
from time import time
import logging
logger = logging.getLogger(__name__)

class sleight_model:
	# https://github.com/tensorflow/models/blob/1af55e018eebce03fb61bba9959a04672536107d/research/autoencoder/autoencoder_models/DenoisingAutoencoder.py
	def __init__(self, name, n_inputs=1, n_steps=50, n_outputs=1, n_neurons=100, n_layers=1, \
		 learning_rate=0.001, dropout=0, cell_type='rnn', reg_kernel=False, reg_bias=False, \
		 reg_activity=False, l1=0, l2=0, bidirectional=False, merge_mode='concat', \
		 stateful=False, hidden_list=[], save_dir='.'):
		self.name = name # Name of the model
		self.n_inputs = n_inputs # Number of input features
		self.n_outputs = n_outputs # Number of outputs
		self.n_steps = n_steps # Size of input sequence
		self.n_neurons = n_neurons # Number of neurons per RNN cell
		self.n_layers = n_layers # Number of RNN layers
		self.learning_rate = learning_rate # Learning rate of the optimization algorithm
		self.dropout = dropout # Dropout rate
		self.cell_type = cell_type # Type of RNN cells to use
		self.gpu = self._detect_gpu()
		# https://keras.io/regularizers/
		self.reg_kernel = reg_kernel # Use kernel regularization
		self.reg_bias = reg_bias # Use bias regularization
		self.reg_activity = reg_activity # Use activity regularization
		self.l1, self.l2 = l1, l2 # Store the l1 and l2 rates
		# Recurrent properties
		self.bidirectional = bidirectional # each RNN layer is bidirectional
		self.merge_mode = None if merge_mode == 'none' else merge_mode
		self.stateful = stateful # Whether the batches are stateful
		# supported cell types
		self.cell_options = {'rnn':RNN, 'lstm':CuDNNLSTM if self.gpu else LSTM}
		# Additional layers
		self.hidden_list = hidden_list # List of hidden layer sizes
		# TODO remake this name
		self.param_name = self._gen_name()
		if save_dir[0] == '/':
			self.save_dir = save_dir
		else:
			self.save_dir = os.path.join(os.getcwd(), save_dir)
		self.save_file = os.path.join(self.save_dir, '%s.h5'%(self.param_name))
		self.graph = tf.Graph() # Init graph
		logger.debug("Created graph")
		######################################
		# Set the RNG seeds
		######################################
		np.random.seed(42)
		tf.set_random_seed(42)
		######################################
		# Configure the session
		######################################
		tacc_nodes = {'knl':(136,2), 'skx':(48,2)}
		if os.getenv('TACC_NODE_TYPE', False) in tacc_nodes:
			intra, inter = tacc_nodes[os.getenv('TACC_NODE_TYPE')]
			logger.debug("Using config for TACC %s node (%i, %i)"%(os.getenv('TACC_NODE_TYPE'), intra, inter))
			os.putenv('KMP_BLOCKTIME', '0')
			os.putenv('KMP_AFFINITY', 'granularity=fine,noverbose,compact,1,0')
			os.putenv('OMP_NUM_THREADS', str(intra))
			config = tf.ConfigProto(intra_op_parallelism_threads=intra, \
					inter_op_parallelism_threads=inter)
					#allow_soft_placement=True, device_count = {'CPU': intra})
			sess = tf.Session(config=config)
			set_session(sess)  # set this TensorFlow session as the default session for Keras
		elif self.gpu:
			config = tf.ConfigProto()
			logger.debug("Allowing memory growth on GPU")
			config.gpu_options.allow_growth = True  # dynamically grow the memory used on the GPU
			#config.log_device_placement = True  # to log device placement (on which device the operation ran)
			sess = tf.Session(config=config)
			set_session(sess)  # set this TensorFlow session as the default session for Keras
		else:
			logger.debug("Using default config")
		######################################
		# Build graph
		######################################
		self.model = Sequential()
		for i in self.n_layers:
			self.model.add(self._gen_rnn_layer(i))
			# Add dropout between layers
			if self.dropout:
				self.model.add(Dropout(1.0-self.training_keep))
		# Handel hidden layers
		for hidden_neurons in hidden_list:
			self.model.add(Dense(hidden_neurons, activation='relu'))
		# Final
		self.model.add(layers.Dense(self.n_outputs, activation=None))
		######################################
		# Define optimizer and compile
		######################################
		loss_functions = ('mean_squared_error', 'mean_squared_logarithmic_error', 'categorical_crossentropy')
		loss_func = loss_functions[1]
		#opt = Adam(self.learning_rate)
		opt = RMSprop(self.learning_rate)
		if hvd:
			opt = hvd.DistributedOptimizer(opt)
			self.callbacks = [hvd.callbacks.BroadcastGlobalVariablesCallback(0)]
		self.model.compile(loss=loss_func, optimizer=opt, metrics=['accuracy'])
		# Done
	def _gen_name(self):
		out_name = "%s_s%ix%i_o%i"%(name, self.n_steps, self.n_inputs, self.n_outputs)
		cell_prefix = 'bi' if self.bidirectional else ''
		out_name += "_%ix%s%s%i"%(self.n_layers, cell_prefix, self.cell_type, self.n_neurons)
		if cell_prefix:
			out_name += '_merge-%s'%(str(self.merge_mode))
		out_name += "_state%s"%('T' if self.stateful else 'F')
		out_name += "_learn%s_drop%s"%(str(self.learning_rate), str(self.dropout))
		if (self.reg_kernel or self.reg_bias or self.reg_activity) and (self.l1 or self.l2):
			reg_str = "reg"
			reg_str += 'K' if self.reg_kernel else ''
			reg_str += 'B' if self.reg_bias else ''
			reg_str += 'A' if self.reg_activity else ''
			if self.l1:
				if self.l2:
					reg_str += '-l1_l2(%s)'%(str(self.l1))
				else:
					reg_str += '-l1(%s)'%(str(self.l1))
			elif self.l2:
				reg_str += '-l2(%s)'%(str(self.l2))
		
			out_name += reg_str
		if hidden_list:
			out_name += '_'+'h'.join(map(str, self.hidden_list))
		return out_name
	def _gen_rnn_layer(self, num=0):
		cell_func = self.cell_options[self.cell_type]
		if num == 0:
			input_shape = (self.n_steps, self.n_inputs)
			if self.bidirectional:
				return Bidirectional(cell_func(self.n_neurons, return_sequences=True), merge_mode=self.merge_mode, input_shape=input_shape)
			else:
				return cell_func(self.n_neurons, return_sequences=True, input_shape=input_shape)
		else:
			if self.bidirectional:
				return Bidirectional(cell_func(self.n_neurons, return_sequences=True), merge_mode=self.merge_mode)
			else:
				return cell_func(self.n_neurons, return_sequences=True)
	def _gen_reg(self, name):
		if name == 'kernel' and self.reg_kernel:
			return self_gen_l1_l2()
		elif name == 'bias' and self.reg_bias:
			return self_gen_l1_l2()
		elif name == 'activity' and self.reg_activity:
			return self_gen_l1_l2()
		else:
			return None
	def _gen_l1_l2(self):
		if self.l1:
			if self.l2:
				return l1_l2(self.l1)
			return l1(self.l1)
		elif self.l2:
			return l2(self.l2)
		else:
			return None
	def _detect_gpu(self):
		return "GPU" in [d.device_type for d in device_lib.list_local_devices()]
	def save(self):
		if not hvd or hvd.rank() == 0:
			if not os.path.exists(self.save_dir):
				os.makedirs(self.save_dir)
			self.model.save_weights(self.save_file)
	def restore(self):
		self.model.load_weights(self.save_file)
		logger.debug("Restored model from %s"%(self.save_file))
#	def train(self, x_batch, y_batch):
#		with self.graph.as_default():
#			start_time = time()
#			#self.sess.run(self.bcast)
#			opt_ret, mse = self.sess.run([self.training_op, self.loss], \
#				feed_dict={self.X:x_batch, self.Y:y_batch, \
#						self.keep_p:self.training_keep})
#			end_time = time()
#			total_time = end_time - start_time
#			logger.debug("Finished training batch in %.1f seconds (%.1f sequences/second)"%(total_time, len(x_batch)/total_time))
#		return (mse, total_time)
#	def predict(self, x_batch, render=False):
#		with self.graph.as_default():
#			# The shape is now probably wrong for this
#			y_pred = np.abs(self.sess.run(self.logits, \
#					feed_dict={self.X:x_batch, self.keep_p:1.0}).round(0))
#			y_pred = y_pred.astype(np.uint8)
#		if render:
#			print("Model: %s"%(self.name))
#			# render results
#			#renderResults(x_batch.flatten(), y_batch.flatten(), y_pred)
#		return y_pred

def mem_usage():
	process = psutil.Process(os.getpid())
	return process.memory_info().rss/1000000

def reset_graph(seed=42):
	tf.reset_default_graph()
	tf.set_random_seed(seed)
	np.random.seed(seed)

if __name__ == "__main__":
	main()
