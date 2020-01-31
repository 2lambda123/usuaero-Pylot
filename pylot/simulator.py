# Class containing the simulator

import math as m
import numpy as np
import multiprocessing as mp
from .helpers import *
from .airplanes import MachUpXAirplane, LinearizedAirplane
import json
import copy
import time
import pygame.display
import pygame.image
from pygame.locals import HWSURFACE, OPENGL, DOUBLEBUF
from OpenGL.GL import glClear, glClearColor
from .graphics import *
import os

class Simulator:
    """A class for flight simulation using RK4 integration.

    Parameters
    ----------
    input_dict : dict
        Dictionary describing the simulation and world parameters.
    """

    def __init__(self, input_dict):

        # Print welcome
        print("\n--------------------------------------------------")
        print("              Welcome to Pylot!                   ")
        print("                 USU AeroLab                      ")
        print("--------------------------------------------------")

        # Store input
        self._input_dict = input_dict
        self._units = self._input_dict.get("units", "English")

        # Get simulation parameters
        self._real_time = self._input_dict["simulation"].get("real_time", True)
        self._t0 = self._input_dict["simulation"].get("start_time", 0.0)
        self._tf = self._input_dict["simulation"].get("final_time", np.inf)
        self._render_graphics = self._input_dict["simulation"].get("enable_graphics", False)
        if not self._real_time:
            self._dt = self._input_dict["simulation"].get("dt", 0.01)

        # Initialize inter-process communication
        self._manager = mp.Manager()
        self._state_manager = self._manager.list()
        self._state_manager[:] = [0.0]*16
        self._quit = self._manager.Value('i', 0)
        self._pause = self._manager.Value('i', 0)
        if self._render_graphics:
            self._graphics_ready = self._manager.Value('i', 0)
            self._view = self._manager.Value('i', 1)
            self._flight_data = self._manager.Value('i', 0)
            self._aircraft_graphics_info = self._manager.dict()
            self._control_settings = self._manager.dict()

        # Kick off physics process
        self._physics_process = mp.Process(target=self._run_physics, args=())

        # Initialize pygame modules
        pygame.init()

        # Initialize graphics
        if self._render_graphics:
            self._initialize_graphics()

        # Number of pilot views available
        self._num_views = 2


    def _initialize_graphics(self):
        # Initializes the graphics

        # Get path to graphics objects
        self._pylot_path = os.path.dirname(__file__)
        self._graphics_path = os.path.join(self._pylot_path,os.path.pardir,"graphics")
        self._cessna_path = os.path.join(self._graphics_path, "Cessna")
        self._res_path = os.path.join(self._graphics_path, "res")
        self._shaders_path = os.path.join(self._graphics_path, "shaders")

        # Setup window size
        display_info = pygame.display.Info()
        self._width = display_info.current_w
        self._height = display_info.current_h
        pygame.display.set_icon(pygame.image.load(os.path.join(self._res_path, 'gameicon.jpg')))
        self._screen = pygame.display.set_mode((self._width,self._height), HWSURFACE|OPENGL|DOUBLEBUF)
        pygame.display.set_caption("Pylot Flight Simulator, (C) USU AeroLab")
        glViewport(0,0,self._width,self._height)
        glEnable(GL_DEPTH_TEST)
        
        # Get target framerate
        self._target_framerate = self._input_dict["simulation"].get("target_framerate", 30)

        # Render loading screen
        glClearColor(0.,0.,0.,1.0)
        glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT)
        loading = Text(150)
        loading.draw(-0.2,-0.05,"Loading...",(0,255,0,1))
        pygame.display.flip()

        # Initialize game over screen
        self._gameover = Text(150)

        # Initialize HUD
        self._HUD = HeadsUp(self._width, self._height, self._res_path, self._shaders_path, self._screen)

        # Initialize flight data overlay
        self._data = FlightData(self._units)
        self._stall_warning = Text(100)

        # Initialize ground
        self._ground_quad = []
        self._quad_size = 20000
        self._ground_positions = [[0., 0., 0.],
                                  [0., self._quad_size, 0.],
                                  [self._quad_size, 0., 0.],
                                  [self._quad_size, self._quad_size, 0.]]
        ground_orientations = [[1., 0., 0., 0.],
                               [0., 0., 0., 1.],
                               [0., 0., 1., 0.],
                               [0., 1., 0., 0.]] # I'm not storing these because they don't change
        for i in range(4):
            self._ground_quad.append(Mesh(
                os.path.join(self._res_path, "field.obj"),
                os.path.join(self._shaders_path, "field.vs"),
                os.path.join(self._shaders_path, "field.fs"),
                os.path.join(self._res_path, "field_texture.jpg"),
                self._width,
                self._height))
            self._ground_quad[i].set_position(self._ground_positions[i])
            self._ground_quad[i].set_orientation(ground_orientations[i])

        # Initialize camera object
        self._cam = Camera()

        # Clock object for tracking frames and timestep
        self._clock = pygame.time.Clock()

        # Ticks clock before starting game loop
        self._clock.tick_busy_loop()


    def _load_aircraft(self):
        # Loads the aircraft from the input file

        # Read in aircraft input
        aircraft_name = self._input_dict["aircraft"]["name"]
        aircraft_file = self._input_dict["aircraft"]["file"]
        with open(aircraft_file, 'r') as aircraft_file_handle:
            aircraft_dict = json.load(aircraft_file_handle)

        # Get density model, controller, and output file
        density = import_value("density", self._input_dict.get("atmosphere", {}), self._units, [0.0023769, "slug/ft^3"])

        # Linear aircraft
        if aircraft_dict["aero_model"]["type"] == "linearized_coefficients":
            self._aircraft = LinearizedAirplane(aircraft_name, aircraft_dict, density, self._units, self._input_dict["aircraft"])
        
        # MachUpX aircraft
        else:
            self._aircraft = MachUpXAirplane(aircraft_name, aircraft_dict, density, self._units, self._input_dict["aircraft"])


    def _run_physics(self):
        # Handles physics on a separate process

        # Initialize pause flag
        self._paused = False

        # Load aircraft
        self._load_aircraft()

        # Pass airplane graphics information to parent process
        if self._render_graphics:
            aircraft_graphics_info = self._aircraft.get_graphics_info()
            for key, value in aircraft_graphics_info.items():
                self._aircraft_graphics_info[key] = value

            # Give initial state to graphics
            self._aircraft_graphics_info["position"] = self._aircraft.y[6:9]
            self._aircraft_graphics_info["orientation"] = self._aircraft.y[9:]

            # Wait for graphics to load
            while not self._graphics_ready.value:
                continue

        # Get an initial guess for how long each sim step is going to take
        t0 = time.time()
        if self._real_time:
            self._RK4(self._aircraft, self._t0, 0.0)
            self._aircraft.normalize()
            self._aircraft.output_state(self._t0)
            t1 = time.time()
            self._dt = t1-t0
            t0 = t1
        else:
            self._aircraft.output_state(self._t0)

        t = copy.copy(self._t0)

        # Simulation loop
        while t <= self._tf and not self._quit.value:

            # Integrate
            self._RK4(self._aircraft, t, self._dt)

            # Normalize
            self._aircraft.normalize()

            # Step in time
            if self._real_time:
                t1 = time.time()
                self._dt = t1-t0
                t0 = t1
            t += self._dt

            # Write output
            self._aircraft.output_state(t)

            # Handle graphics only things
            if self._render_graphics:

                # Pass information to graphics
                self._state_manager[:13] = self._aircraft.y[:]
                self._state_manager[13] = self._dt
                self._state_manager[14] = t
                self._state_manager[15] = t1
                for key, value in self._aircraft._controls.items():
                    self._control_settings[key] = value

                while True:
                    # Parse inputs
                    inputs = self._aircraft._controller.get_input()
                    if inputs.get("pause", False):
                        self._pause.value = not self._pause.value
                    if inputs.get("data", False):
                        self._flight_data.value = not self._flight_data.value
                    if inputs.get("quit", False):
                        self._quit.value = not self._quit.value
                    if inputs.get("view", False):
                        self._view.value = (self._view.value+1)%self._num_views

                    # Pause
                    if self._pause.value and not self._paused:
                        self._paused = True
                        self._state_manager[13] = 0.0 # The physics isn't stepping...

                    # Break out of pause
                    if not self._pause.value:
                        if self._paused:
                            self._paused = False
                            if self._real_time:
                                t0 = time.time() # So as to not throw off the integration
                        break

        # If we exit the loop due to a timeout, let the graphics know we're done
        self._quit.value = 1


    def run_sim(self):
        """Runs the simulation according to the defined inputs.
        """

        # Kick off the physics
        self._physics_process.start()

        # Get graphics going
        if self._render_graphics:

            # Wait for physics to initialize then import aircraft object
            while True:
                try:
                    # Get graphics files
                    obj_path = self._aircraft_graphics_info["obj_file"]
                    v_shader_path = self._aircraft_graphics_info["v_shader_file"]
                    f_shader_path = self._aircraft_graphics_info["f_shader_file"]
                    texture_path = self._aircraft_graphics_info["texture_file"]

                    # Initialize graphics object
                    self._aircraft_graphics = Mesh(obj_path, v_shader_path, f_shader_path, texture_path, self._width, self._height)
                    self._aircraft_graphics.set_position(self._aircraft_graphics_info["position"])
                    self._aircraft_graphics.set_orientation(self._aircraft_graphics_info["orientation"])

                    # Get reference lengths for setting camera offset
                    self._bw = self._aircraft_graphics_info["l_ref_lat"]
                    self._cw = self._aircraft_graphics_info["l_ref_lon"]

                    break

                except KeyError: # If it's not there, just keep waiting
                    continue

            # Let the physics know we're good to go
            self._graphics_ready.value = 1

            # Run graphics loop
            while not self._quit.value:

                # Update graphics
                self._update_graphics()

        # Wait for the physics to finish
        self._physics_process.join()
        self._physics_process.close()
        self._manager.shutdown()

        # Print quit message
        print("\n--------------------------------------------------")
        print("           Pylot exited successfully.             ")
        print("                  Thank you!                      ")
        print("--------------------------------------------------")


    def _update_graphics(self):
        # Does a step in graphics

        # Set default background color for sky
        glClearColor(0.65,1.0,1.0,1.0)

        # Clear GL buffers
        glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT|GL_ACCUM_BUFFER_BIT|GL_STENCIL_BUFFER_BIT)

        # Check for quitting
        if self._quit.value:
            return True

        # Get state from state manager
        y = np.array(copy.deepcopy(self._state_manager[:13]))

        # Check to see if the physics has finished the first loop
        if (y == 0.0).all():
            return False

        # Get timing information from physics
        dt_physics = self._state_manager[13]
        t_physics = self._state_manager[14]
        graphics_delay = time.time()-self._state_manager[15] # Included to compensate for the fact that these physics results may be old or brand new

        # Graphics timestep
        dt_graphics = self._clock.tick(self._target_framerate)/1000.

        # Update aircraft position and orientation
        self._aircraft_graphics.set_orientation(swap_quat(y[9:]))
        self._aircraft_graphics.set_position(y[6:9])

        # Get flight data
        flight_data = self._get_flight_data(y, dt_graphics, dt_physics, t_physics)

        # Check for crashing into the ground
        if y[8] > 0.0:
            glClearColor(0,0,0,1.0)
            glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT)

            # Display Game Over screen and quit physics
            self._gameover.draw(-0.2,-0.05,"Game Over",(0,255,0,1))
            self._quit.value = 1
	
        # Otherwise, render graphics
        else:
            # Third person view
            if self._view.value == 0:
                view = self._cam.third_view(self._aircraft_graphics, t_physics, graphics_delay, y[0], offset=[-self._bw, 0.0, -self._cw])
                self._aircraft_graphics.set_view(view)
                self._aircraft_graphics.render()
	
            # Cockpit view
            elif self._view.value == 1:
                self._cam.pos_storage.clear()
                self._cam.up_storage.clear()
                self._cam.target_storage.clear()
                self._cam.time_storage.clear()
                view = self._cam.cockpit_view(self._aircraft_graphics)
                self._HUD.render(y[:3], self._aircraft_graphics, view)

            # Determine aircraft displacement in quad widths
            x_pos = y[6]
            y_pos = y[7]
            if x_pos > 0.0:
                N_quads_x = (x_pos+self._quad_size//2)//self._quad_size
            else:
                N_quads_x = (x_pos-self._quad_size//2)//self._quad_size+1
            if y_pos > 0.0:
                N_quads_y = (y_pos+self._quad_size//2)//self._quad_size
            else:
                N_quads_y = (y_pos-self._quad_size//2)//self._quad_size+1

            # Set positions based on aircraft position within the current quad
            curr_quad_x = N_quads_x*self._quad_size
            curr_quad_y = N_quads_y*self._quad_size

            if x_pos > curr_quad_x and y_pos > curr_quad_y:
                self._ground_positions = [[N_quads_x*self._quad_size, N_quads_y*self._quad_size, 0.],
                                          [N_quads_x*self._quad_size, (N_quads_y+1)*self._quad_size, 0.],
                                          [(N_quads_x+1)*self._quad_size, N_quads_y*self._quad_size, 0.],
                                          [(N_quads_x+1)*self._quad_size, (N_quads_y+1)*self._quad_size, 0.]]
            elif x_pos < curr_quad_x and y_pos > curr_quad_y:
                self._ground_positions = [[N_quads_x*self._quad_size, N_quads_y*self._quad_size, 0.],
                                          [N_quads_x*self._quad_size, (N_quads_y+1)*self._quad_size, 0.],
                                          [(N_quads_x-1)*self._quad_size, N_quads_y*self._quad_size, 0.],
                                          [(N_quads_x-1)*self._quad_size, (N_quads_y+1)*self._quad_size, 0.]]
            elif x_pos < curr_quad_x and y_pos < curr_quad_y:
                self._ground_positions = [[N_quads_x*self._quad_size, N_quads_y*self._quad_size, 0.],
                                          [N_quads_x*self._quad_size, (N_quads_y-1)*self._quad_size, 0.],
                                          [(N_quads_x-1)*self._quad_size, N_quads_y*self._quad_size, 0.],
                                          [(N_quads_x-1)*self._quad_size, (N_quads_y-1)*self._quad_size, 0.]]
            else:
                self._ground_positions = [[N_quads_x*self._quad_size, N_quads_y*self._quad_size, 0.],
                                          [N_quads_x*self._quad_size, (N_quads_y-1)*self._quad_size, 0.],
                                          [(N_quads_x+1)*self._quad_size, N_quads_y*self._quad_size, 0.],
                                          [(N_quads_x+1)*self._quad_size, (N_quads_y-1)*self._quad_size, 0.]]

            # Swap tiling to keep the order the same
            if (N_quads_x%2 != 0):
                self._ground_positions[0], self._ground_positions[2] = self._ground_positions[2], self._ground_positions[0]
                self._ground_positions[1], self._ground_positions[3] = self._ground_positions[3], self._ground_positions[1]
            if (N_quads_y%2 != 0):
                self._ground_positions[0], self._ground_positions[1] = self._ground_positions[1], self._ground_positions[0]
                self._ground_positions[2], self._ground_positions[3] = self._ground_positions[3], self._ground_positions[2]

            # Update ground graphics
            for i, quad in enumerate(self._ground_quad):
                quad.set_position(self._ground_positions[i])
                quad.set_view(view)
                quad.render()

            # Check for the aerodynamic model falling apart
            if np.isnan(y[0]):
                error_msg = Text(100)
                error_msg.draw(-1.0, 0.5, "Pylot encountered a physics error...", color=(255,0,0,1))

            # Display flight data
            elif self._flight_data.value:
                self._data.render(flight_data, self._control_settings)

        # Update screen display
        pygame.display.flip()


    def _get_flight_data(self, y, dt_graphics, dt_physics, t_physics):
        # Parses state of aircraft
        u = y[0]
        v = y[1]
        w = y[2]
        V = sqrt(u*u+v*v+w*w)
        E = np.degrees(Quat2Euler(y[9:]))
        V_f = Body2Fixed(y[:3], y[9:])
        a = m.atan2(w,u)
        B = m.atan2(v,u)

        # Store data
        flight_data = {
            "Graphics Time Step" : dt_graphics,
            "Physics Time Step" : dt_physics,
            "Airspeed" : V,
            "AoA" : m.degrees(a),
            "Sideslip" : m.degrees(B),
            "Altitude" : -y[8],
            "Latitude" : y[6]/131479714.0*360.0,
            "Longitude" : y[7]/131259396.0*360.0,
            "Time" : t_physics,
            "Bank" : E[0],
            "Elevation" : E[1],
            "Heading" : E[2],
            "Gnd Speed" : m.sqrt(V_f[0]*V_f[0]+V_f[1]*V_f[1])*0.68181818181818181818,
            "Gnd Track" : E[2],
            "Climb" : -V_f[2]*60,
            "Axial G-Force" : 0.0,
            "Side G-Force" : 0.0,
            "Normal G-Force" : 0.0,
            "Roll Rate" : m.degrees(y[3]),
            "Pitch Rate" : m.degrees(y[4]),
            "Yaw Rate" : m.degrees(y[5])
        }
    
        return flight_data


    def _RK4(self, aircraft, t, dt):
        """Performs Runge-Kutta integration for the given aircraft.

        Parameters
        ----------
        aircraft : BaseAircraft
            Aircraft to integrate the state of.

        t : float
            Initial time.

        dt : float
            Time step.

        """
        y0 = copy.deepcopy(aircraft.y)

        # Determine k0
        k0 = aircraft.dy_dt(t)

        # Determine k1
        aircraft.y = y0+0.5*dt*k0 
        k1 = aircraft.dy_dt(t+0.5*dt)

        # Determine k2
        aircraft.y = y0+0.5*dt*k1
        k2 = aircraft.dy_dt(t+0.5*dt)

        # Determine k3
        aircraft.y = y0+dt*k2
        k3 = aircraft.dy_dt(t+dt)

        # Calculate y
        aircraft.y = y0+0.16666666666666666666667*(k0+2*k1+2*k2+k3)*dt